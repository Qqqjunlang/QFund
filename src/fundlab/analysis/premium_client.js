/* 离线交互图的纯计算模块；不读取 DOM、不请求网络、不改写输入。 */
(function (global) {
  "use strict";
  function decimalParts(value) {
    if (typeof value !== "string" || !/^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/.test(value.trim())) {
      throw new Error("请输入有效溢价率数值，例如 12.5 表示 12.5%，可输入零或负数。");
    }
    const text = value.trim(), numeric = Number(text);
    if (!Number.isFinite(numeric) || (numeric === 0 && /[1-9]/.test(text))) {
      throw new Error("请输入可计算的溢价率数值。");
    }
    const [integerRaw, fractionRaw = ""] = text.replace(/^[+-]/, "").split(".");
    const integer = integerRaw.replace(/^0+/, "") || "0", fraction = fractionRaw.replace(/0+$/, "");
    const sign = integer === "0" && !fraction ? 0 : text.startsWith("-") ? -1 : 1;
    return {integer, fraction, sign, canonical: (sign < 0 ? "-" : "") + integer + (fraction ? "." + fraction : "")};
  }
  function decimalCompare(a, b) {
    const left = decimalParts(a), right = decimalParts(b);
    if (left.sign !== right.sign) return left.sign < right.sign ? -1 : 1;
    if (!left.sign) return 0;
    let result;
    if (left.integer.length !== right.integer.length) result = left.integer.length < right.integer.length ? -1 : 1;
    else if (left.integer !== right.integer) result = left.integer < right.integer ? -1 : 1;
    else {
      const size = Math.max(left.fraction.length, right.fraction.length);
      const x = left.fraction.padEnd(size, "0"), y = right.fraction.padEnd(size, "0");
      result = x === y ? 0 : x < y ? -1 : 1;
    }
    return result * left.sign;
  }
  function compute(points, start, end, periods, comparisonPremium = null) {
    const custom = comparisonPremium === null ? null : decimalParts(comparisonPremium).canonical;
    if (!periods.length || periods.some(n => !Number.isSafeInteger(n) || n < 1)) {
      throw new Error("N 日周期须为正整数，且不超过 9007199254740991。");
    }
    periods = [...new Set(periods)];
    const sums = [0], history = [];
    let current = null, latestRaw = null;
    const all = points.map(point => {
      const averages = Object.fromEntries(periods.map(n => [String(n), null]));
      const premium = point.premium === null ? null : Number(point.premium);
      if (premium !== null) {
        history.push(premium);
        sums.push(sums[sums.length - 1] + premium);
        for (const n of periods) {
          if (history.length >= n) averages[n] = (sums[history.length] - sums[history.length - n]) / n;
        }
      }
      const row = {price_date: point.date, premium, average_premiums: averages};
      if (premium !== null) { current = row; latestRaw = point.premium; }
      return row;
    });
    const selected = all.filter(point => point.price_date >= start && point.price_date <= end);
    const values = selected.filter(point => point.premium !== null).map(point => point.premium).sort((a,b) => a-b);
    const quantiles = {};
    for (const q of [10,30,50,70,90]) {
      if (!values.length) { quantiles[q] = null; continue; }
      const position = (values.length - 1) * q / 100, lower = Math.floor(position), upper = Math.min(lower + 1, values.length - 1);
      quantiles[q] = values[lower] + (values[upper] - values[lower]) * (position - lower);
    }
    const target = custom === null ? latestRaw : custom;
    const rankSamples = points.filter(point => point.date >= start && point.date <= end && point.premium !== null);
    const comparisonRank = rankSamples.length && target !== null ? rankSamples.filter(p => decimalCompare(p.premium, target) <= 0).length / rankSamples.length * 100 : null;
    return {series: selected, quantiles, current,
      comparison: {mode: custom === null ? "current" : "custom", premium: target,
        price_date: custom === null && current ? current.price_date : null, percentile_rank: comparisonRank},
      percentile_rank: values.length && current ? values.filter(v => v <= current.premium).length / values.length * 100 : null,
      rows: selected.length, valid_samples: values.length, total_valid_samples: history.length,
      missing_samples: selected.length - values.length, periods};
  }
  global.FundlabPremiumStats = Object.freeze({compute});
})(window);
