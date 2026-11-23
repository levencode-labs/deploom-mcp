// Pinned host TypeScript parses strings. Project files/modules are never executed.
'use strict';
const fs=require('node:fs');
const ts=require(process.argv[2]);
const input=process.argv[3];
if(fs.statSync(input).size>2*1024*1024)throw Error('USAGE_INPUT_TOO_LARGE');
const payload=JSON.parse(fs.readFileSync(input,'utf8'));
if(!Array.isArray(payload.files)||payload.files.length>256||!Array.isArray(payload.packages))throw Error('INVALID_USAGE_INPUT');
const targets=new Set(payload.packages),observations=[];
const counts=new Map();
let parseErrors=0,dynamicLoads=0,aliasLoads=0,dynamicConfigs=0,omittedObservations=0,omittedSymbols=0;
function targetOf(specifier){
 const parts=specifier.split('/');
 const name=parts.slice(0,specifier.startsWith('@')?2:1).join('/');
 return targets.has(name)?name:null;
}
function literal(node){return node&&(ts.isStringLiteral(node)||ts.isNoSubstitutionTemplateLiteral(node))?node.text:null;}
function importedNames(bindings){
 const names=bindings?.elements?.filter(element=>!element.isTypeOnly&&!element.dotDotDotToken)
  .map(element=>(element.propertyName||element.name).text).filter(name=>typeof name==='string')||[];
 const selected=names.filter(name=>name.length<=80).slice(0,12);
 omittedSymbols+=names.length-selected.length;
 return selected;
}
function nameOf(node){return ts.isIdentifier(node)||ts.isStringLiteral(node)?node.text:null;}
function propertyPath(node){
 const parts=[];let current=node;
 while(current&&!ts.isSourceFile(current)){
  if(ts.isPropertyAssignment(current)){const part=nameOf(current.name);if(part===null)return null;parts.unshift(part);}
  if(ts.isCallExpression(current))return {parts,call:current};
  if(ts.isArrowFunction(current)||ts.isFunctionExpression(current))return null;
  current=current.parent;
 }
 return null;
}
for(const file of payload.files){
 const ext=file.path.toLowerCase();
 const kind=ext.endsWith('.tsx')?ts.ScriptKind.TSX:ext.endsWith('.jsx')?ts.ScriptKind.JSX:/\.(?:[cm]?ts)$/.test(ext)?ts.ScriptKind.TS:ts.ScriptKind.JS;
 const source=ext.endsWith('.json')?ts.parseJsonText(file.path,file.text):ts.createSourceFile(file.path,file.text,ts.ScriptTarget.Latest,true,kind);
 if(source.parseDiagnostics.length){parseErrors+=source.parseDiagnostics.length;continue;}
 const viteConfigNames=new Map(),moduleBindings=new Map();
 let checker;
 function localSymbol(node){
  if(!checker){
   // An in-memory host bounds lookup to this one already-parsed file.
   const options={allowJs:true,noResolve:true,noLib:true,types:[],target:ts.ScriptTarget.Latest};
   const host={
    getSourceFile:name=>name===file.path?source:undefined,
    getDefaultLibFileName:()=>'',getCurrentDirectory:()=>'',getCanonicalFileName:name=>name,
    fileExists:name=>name===file.path,readFile:()=>undefined,useCaseSensitiveFileNames:()=>true,
    writeFile:()=>{throw Error('READ_ONLY_SYNTAX_ANALYSIS');},getNewLine:()=>'\n',
   };
   checker=ts.createProgram([file.path],options,host).getTypeChecker();
  }
  return checker.getSymbolAtLocation(node);
 }
 function isViteConfigCall(node){
  const expression=node.expression;
  if(!ts.isIdentifier(expression))return false;
  const imported=viteConfigNames.get(expression.text);
  return !!imported&&imported===localSymbol(expression);
 }
 function add(node,packageName,form,extra={}){
  const count=counts.get(packageName)||0;
  if(count>=16||observations.length>=48||file.path.length>512||extra.specifier?.length>240){
   omittedObservations++;return;
  }
  counts.set(packageName,count+1);
  const position=source.getLineAndCharacterOfPosition(node.getStart(source));
  observations.push({path:file.path,line:position.line+1,column:position.character+1,package:packageName,form,
   authority:'OBSERVED_SYNTAX_ONLY',semanticApplicability:'UNKNOWN',...extra});
 }
 for(const [packageName,keys] of Object.entries(file.configKeys||{})){
  add(source,packageName,'CONFIG_FILE');
  function visitConfig(node,prefix=[]){
   if(!ts.isObjectLiteralExpression(node))return;
   for(const property of node.properties){
    if(!ts.isPropertyAssignment(property)||nameOf(property.name)===null){dynamicConfigs++;continue;}
    const part=nameOf(property.name);
    if(part.includes('.'))continue;
    const path=[...prefix,part],key=path.join('.');
    if(keys.includes(key))add(property,packageName,'CONFIG_KEY',{key});
    if(keys.some(selector=>selector.startsWith(key+'.'))){
     if(ts.isObjectLiteralExpression(property.initializer))visitConfig(property.initializer,path);
     else dynamicConfigs++;
    }
   }
  }
  if(!keys.length)continue;
  for(const statement of source.statements){
   let expression=statement.expression;
   if(ts.isExpressionStatement(statement)&&expression&&ts.isBinaryExpression(expression)
     &&expression.operatorToken.kind===ts.SyntaxKind.EqualsToken&&ts.isPropertyAccessExpression(expression.left)
     &&ts.isIdentifier(expression.left.expression)&&expression.left.expression.text==='module'&&expression.left.name.text==='exports'){
    expression=expression.right;
   }else if(!ext.endsWith('.json')&&!ts.isExportAssignment(statement))continue;
   if(!expression)continue;
   if(ts.isCallExpression(expression))expression=expression.arguments[0];
   if(expression&&ts.isObjectLiteralExpression(expression))visitConfig(expression);
   else dynamicConfigs++;
  }
 }
 for(const statement of source.statements){
  if(ts.isImportDeclaration(statement)&&!statement.importClause?.isTypeOnly){
   const specifier=literal(statement.moduleSpecifier),packageName=specifier&&targetOf(specifier);
   if(packageName){
    const clause=statement.importClause;
    const namespace=clause?.namedBindings;
    for(const binding of [clause?.name,namespace&&ts.isNamespaceImport(namespace)?namespace.name:null]){
     if(binding){
      const symbol=localSymbol(binding);
      if(symbol)moduleBindings.set(symbol,{packageName,specifier});
     }
    }
   }
  }
  if(targets.has('vite')&&ts.isImportDeclaration(statement)&&!statement.importClause?.isTypeOnly
    &&ts.isStringLiteral(statement.moduleSpecifier)&&statement.moduleSpecifier.text==='vite'){
   const bindings=statement.importClause?.namedBindings;
   if(bindings&&ts.isNamedImports(bindings))for(const element of bindings.elements)
    if(!element.isTypeOnly&&(element.propertyName||element.name).text==='defineConfig')
     viteConfigNames.set(element.name.text,localSymbol(element.name));
  }
 }
 function visit(node){
  if(ts.isImportDeclaration(node)||ts.isExportDeclaration(node)){
   const specifier=literal(node.moduleSpecifier),packageName=specifier&&targetOf(specifier);
   if(packageName){
    const isImport=ts.isImportDeclaration(node);
    const bindings=isImport?node.importClause?.namedBindings:node.exportClause;
    const names=importedNames(bindings);
    add(node,packageName,isImport?(node.importClause?.name?'DEFAULT_IMPORT':'MODULE_IMPORT'):'MODULE_REEXPORT',
     {specifier,typeOnly:!!(isImport?node.importClause?.isTypeOnly:node.isTypeOnly),knownImportedSymbols:names,executionOrUseProven:false});
   }
  }
  if(ts.isImportEqualsDeclaration(node)&&ts.isExternalModuleReference(node.moduleReference)){
   const specifier=literal(node.moduleReference.expression),packageName=specifier&&targetOf(specifier);
   if(packageName)add(node,packageName,'IMPORT_EQUALS_REQUIRE',{specifier,typeOnly:!!node.isTypeOnly,executionOrUseProven:false});
  }
  if(moduleBindings.size&&(ts.isPropertyAccessExpression(node)||ts.isElementAccessExpression(node))
    &&ts.isIdentifier(node.expression)){
   const binding=moduleBindings.get(localSymbol(node.expression));
   const symbol=ts.isPropertyAccessExpression(node)?node.name.text:literal(node.argumentExpression);
   if(binding&&symbol&&symbol.length<=80)
    add(node,binding.packageName,'IMPORTED_MEMBER_ACCESS',{specifier:binding.specifier,knownImportedSymbols:[symbol],executionOrUseProven:false});
  }
  if(ts.isCallExpression(node)){
   const isRequire=ts.isIdentifier(node.expression)&&node.expression.text==='require';
   const isImport=node.expression.kind===ts.SyntaxKind.ImportKeyword;
   if(isRequire||isImport){
    const specifier=literal(node.arguments[0]);
    if(specifier===null||(!isImport&&node.arguments.length!==1))dynamicLoads++;
    else{
     const packageName=targetOf(specifier);
     if(packageName&&(!isRequire||!localSymbol(node.expression)?.declarations?.length)){
      const parent=node.parent;
      const names=ts.isVariableDeclaration(parent)&&ts.isObjectBindingPattern(parent.name)?importedNames(parent.name):[];
      if(ts.isPropertyAccessExpression(parent)&&parent.expression===node)names.push(parent.name.text);
      add(node,packageName,isImport?'DYNAMIC_IMPORT_LITERAL':'REQUIRE_LITERAL',
       {specifier,knownImportedSymbols:names,bindingOrRuntimeResolution:'UNKNOWN'});
     }
    }
   }
   if(isViteConfigCall(node)){
    const argument=node.arguments[0];if(!argument||!ts.isObjectLiteralExpression(argument))dynamicConfigs++;
    else if(argument.properties.some(p=>ts.isSpreadAssignment(p)))dynamicConfigs++;
   }
  }
  if(targets.has('vite')&&(ts.isSpreadAssignment(node)||ts.isComputedPropertyName(node))){
   let parent=node.parent;
   while(parent&&!ts.isSourceFile(parent)){
    if(ts.isCallExpression(parent)&&isViteConfigCall(parent)){dynamicConfigs++;break;}
    parent=parent.parent;
   }
  }
  if(ts.isPropertyAssignment(node)&&targets.has('vite')){
   const context=propertyPath(node);
   if(context&&isViteConfigCall(context.call)
     &&context.parts[0]==='css'&&(ts.isIdentifier(node.initializer)||ts.isCallExpression(node.initializer)||ts.isPropertyAccessExpression(node.initializer)))dynamicConfigs++;
  }
  if(ts.isVariableDeclaration(node)&&node.initializer&&ts.isIdentifier(node.initializer)&&node.initializer.text==='require')aliasLoads++;
  if(ts.isPropertyAssignment(node)&&nameOf(node.name)==='api'&&ts.isStringLiteral(node.initializer)&&targets.has('vite')){
   const context=propertyPath(node);
   if(context&&isViteConfigCall(context.call)
     &&['css.preprocessorOptions.scss.api','css.preprocessorOptions.sass.api'].includes(context.parts.join('.'))){
    const allowed=['legacy','modern','modern-compiler'];
    add(node,'vite','VITE_SASS_API_LITERAL',{value:allowed.includes(node.initializer.text)?node.initializer.text:'OTHER_LITERAL',configEvaluation:'NOT_PERFORMED'});
   }
  }
  ts.forEachChild(node,visit);
 }
 visit(source);
}
const cpu=process.cpuUsage();
process.stdout.write(JSON.stringify({parserVersion:ts.version,parserReportedCpuSeconds:(cpu.user+cpu.system)/1000000,observations,parseErrors,dynamicLoads,aliasLoads,dynamicConfigs,omittedObservations,omittedSymbols,
 sourceEvaluationPerformed:false,projectImportsResolved:false}));
