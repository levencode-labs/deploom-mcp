"""Input usage and estimated input attributable to Skill and DepLoom MCP output."""
from collections import Counter


TOOL_FAMILY = {"read_skill": "skill", "deploom_plan": "mcp", "read_deploom_plan": "mcp"}
FAMILIES = ("skill", "mcp")
OUTPUT_FIELDS = ("toolOutputs", "outputBytes", "inputOccurrences", "inputBytes",
                 "countedInputOccurrences", "estimatedInputTokens")


def usage_totals(rows):
    total = sum(row.get("inputTokens", 0) for row in rows)
    cached = sum(row.get("cachedTokens", 0) for row in rows)
    unknown = sum(row.get("cacheUnknownInputTokens",
                          0 if "cachedTokens" in row else row.get("inputTokens", 0)) for row in rows)
    return {"inputTokens": total, "cachedInputTokens": cached,
            "uncachedInputTokens": total - cached - unknown, "cacheUnknownInputTokens": unknown}


def cache_usage(row, usage):
    tokens = usage["input_tokens"]
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens")
    row["inputTokens"] += tokens
    row["inputUsageResponses"] += 1
    if type(cached) is int and 0 <= cached <= tokens:
        row["cachedTokens"] += cached
    else:
        row["cacheUnknownInputTokens"] += tokens


def counted(count_text, text):
    try:
        count = count_text(text)
        return count if type(count) is int and count >= 0 else None
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return None


def finish_family(stats):
    if stats["inputOccurrences"] and not stats["countedInputOccurrences"]:
        stats["estimatedInputTokens"] = None
    return stats


def tool_inputs(outputs, responses, count_text):
    """Estimate isolated token deltas multiplied by actual in-context occurrences."""
    families = {name: dict.fromkeys(OUTPUT_FIELDS, 0) for name in FAMILIES}
    exposure = Counter()
    for family, text, produced_after in outputs:
        stats = families[family]
        occurrences = responses - produced_after
        size = len(text.encode("utf-8"))
        stats["toolOutputs"] += 1
        stats["outputBytes"] += size
        stats["inputOccurrences"] += occurrences
        stats["inputBytes"] += size * occurrences
        if occurrences:
            exposure[family, text] += occurrences

    baseline = counted(count_text, "") if exposure else None
    if baseline is not None:
        counts = {text: counted(count_text, text) for text in dict.fromkeys(text for _, text in exposure)}
        for (family, text), occurrences in exposure.items():
            if counts[text] is not None:
                stats = families[family]
                stats["countedInputOccurrences"] += occurrences
                stats["estimatedInputTokens"] += max(0, counts[text] - baseline) * occurrences
    return {name: finish_family(stats) for name, stats in families.items()}


def aggregate_tool_inputs(rows):
    records = [row["toolInputAttribution"] for row in rows if "toolInputAttribution" in row]
    return {
        "sessionsRecorded": len(records),
        "families": {name: finish_family({field: sum(record[name][field] or 0 for record in records)
                                          for field in OUTPUT_FIELDS})
                     for name in FAMILIES} if records else None,
    }


if __name__ == "__main__":
    row = dict.fromkeys(("inputTokens", "inputUsageResponses", "cachedTokens", "cacheUnknownInputTokens"), 0)
    cache_usage(row, {"input_tokens": 90, "input_tokens_details": {"cached_tokens": 60}})
    cache_usage(row, {"input_tokens": 10})
    assert usage_totals([row, {"inputTokens": 30, "cachedTokens": 20}]) == {
        "inputTokens": 130, "cachedInputTokens": 80, "uncachedInputTokens": 40, "cacheUnknownInputTokens": 10,
    }
    calls = []

    def fake_counter(text):
        calls.append(text)
        return {"": 5, "shared": 12}[text]

    result = tool_inputs([("skill", "shared", 1), ("mcp", "shared", 2),
                          ("mcp", "unavailable", 2), ("mcp", "unsent", 4)], 4, fake_counter)
    assert calls == ["", "shared", "unavailable"]
    assert result["skill"]["estimatedInputTokens"] == 21
    assert result["mcp"]["estimatedInputTokens"] == 14
    assert result["mcp"]["countedInputOccurrences"] == 2
    assert result["mcp"]["inputOccurrences"] == 4
    assert result["mcp"]["inputBytes"] == 34
    assert tool_inputs([], 4, fake_counter)["skill"]["estimatedInputTokens"] == 0
    assert calls == ["", "shared", "unavailable"]
    assert aggregate_tool_inputs([{}])["families"] is None
    assert aggregate_tool_inputs([{}, {"toolInputAttribution": result}])["families"] == result
    print("Token metrics and partial attribution: PASS")
