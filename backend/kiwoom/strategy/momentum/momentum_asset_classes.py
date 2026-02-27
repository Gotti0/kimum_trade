"""
자산군 분류 체계 & 포트폴리오 프리셋 — AssetClassRegistry

설계 문서: docs/글로벌_듀얼_모멤텀_설계계획.md  §2-2

역할:
  - 13개 글로벌 ETF 자산군 메타데이터 중앙 관리
  - 카테고리 그룹핑 (equity / bond / real_asset / cash)
  - 5가지 포트폴리오 프리셋 (성장형 ~ 안정형) 정의
  - 카테고리 ↔ 티커 매핑
"""

from __future__ import annotations

from typing import Any

# ══════════════════════════════════════════════════════
# 1. 자산군(Asset Class) 레지스트리
# ══════════════════════════════════════════════════════

ASSET_CLASSES: dict[str, dict[str, Any]] = {
    # ── 주식 (equity) ──────────────────────────────
    "us_large": {
        "label": "미국 대형주",
        "ticker": "SPY",
        "category": "equity",
        "safe_haven": False,
    },
    "us_small": {
        "label": "미국 소형주",
        "ticker": "IWM",
        "category": "equity",
        "safe_haven": False,
    },
    "dev_ex_us": {
        "label": "선진국 (미국 제외)",
        "ticker": "EFA",
        "category": "equity",
        "safe_haven": False,
    },
    "emerging": {
        "label": "신흥국",
        "ticker": "EEM",
        "category": "equity",
        "safe_haven": False,
    },
    "kr_equity": {
        "label": "한국 주식",
        "ticker": "EWY",
        "category": "equity",
        "safe_haven": False,
        "has_individual_stocks": True,  # 국내 개별종목 Top-N 로직 연동
    },
    # ── 채권 (bond) ────────────────────────────────
    "us_bond_agg": {
        "label": "미국 채권 (종합)",
        "ticker": "AGG",
        "category": "bond",
        "safe_haven": True,
    },
    "us_treasury_mid": {
        "label": "미국 국채 (중기)",
        "ticker": "IEF",
        "category": "bond",
        "safe_haven": True,
    },
    "us_treasury_long": {
        "label": "미국 국채 (장기)",
        "ticker": "TLT",
        "category": "bond",
        "safe_haven": False,       # 장기채는 금리 리스크 — 대피처 부적합
    },
    "tips": {
        "label": "물가연동채",
        "ticker": "TIP",
        "category": "bond",
        "safe_haven": False,
    },
    # ── 실물자산 (real_asset / alternative) ─────────
    "reits": {
        "label": "글로벌 리츠",
        "ticker": "VNQ",
        "category": "real_asset",
        "safe_haven": False,
    },
    "commodity": {
        "label": "원자재",
        "ticker": "DBC",
        "category": "real_asset",
        "safe_haven": False,
    },
    "gold": {
        "label": "금",
        "ticker": "GLD",
        "category": "real_asset",
        "safe_haven": True,
    },
    # ── 현금등가 (cash) ────────────────────────────
    "cash_equiv": {
        "label": "단기 국채 (현금등가)",
        "ticker": "SHY",
        "category": "cash",
        "safe_haven": True,        # 최종 대피처
    },
}

# ── 카테고리 그룹핑 ────────────────────────────────
CATEGORY_GROUPS: dict[str, list[str]] = {
    "equity":     ["us_large", "us_small", "dev_ex_us", "emerging", "kr_equity"],
    "bond":       ["us_bond_agg", "us_treasury_mid", "us_treasury_long", "tips"],
    "real_asset": ["reits", "commodity", "gold"],
    "cash":       ["cash_equiv"],
}

# ── 현금등가 티커 ──────────────────────────────────
CASH_TICKER = "SHY"

# ── 벤치마크 (전통적 60/40) ────────────────────────
BENCHMARK_WEIGHTS: dict[str, float] = {
    "SPY": 0.60,
    "AGG": 0.40,
}


# ══════════════════════════════════════════════════════
# 2. 포트폴리오 프리셋
# ══════════════════════════════════════════════════════

PORTFOLIO_PRESETS: dict[str, dict[str, Any]] = {
    "growth": {
        "label": "성장형",
        "desc": "선진국주식의 비중이 절반 이상. 대체투자와 함께 수익성을 극대화하는 공격적 배분",
        "icon": "🚀",
        "risk_level": 5,
        "weights": {
            "equity":       0.55,
            "alternative":  0.25,
            "foreign_bond": 0.15,
            "domestic_bond": 0.00,
            "cash":         0.05,
        },
    },
    "growth_seeking": {
        "label": "성장추구형",
        "desc": "선진국 주식이 과반 이상. 채권 비중을 축소하며 적극적인 자산 증식 추구",
        "icon": "📈",
        "risk_level": 4,
        "weights": {
            "equity":       0.50,
            "alternative":  0.15,
            "foreign_bond": 0.20,
            "domestic_bond": 0.05,
            "cash":         0.10,
        },
    },
    "balanced": {
        "label": "위험중립형",
        "desc": "선진국주식이 가장 많으나, 해외채권 비중이 커지며 수익과 위험의 균형",
        "icon": "⚖️",
        "risk_level": 3,
        "weights": {
            "equity":       0.35,
            "alternative":  0.15,
            "foreign_bond": 0.30,
            "domestic_bond": 0.10,
            "cash":         0.10,
        },
    },
    "stability_seeking": {
        "label": "안정추구형",
        "desc": "채권 중심 유지. 선진국 주식·대체투자를 일부 편입하여 시중 금리 + 추가 수익 추구",
        "icon": "🛡️",
        "risk_level": 2,
        "weights": {
            "equity":       0.20,
            "alternative":  0.10,
            "foreign_bond": 0.35,
            "domestic_bond": 0.25,
            "cash":         0.10,
        },
    },
    "stable": {
        "label": "안정형",
        "desc": "해외채권이 절반 이상. 채권 위주로 구성하여 안정성을 최우선",
        "icon": "🏦",
        "risk_level": 1,
        "weights": {
            "equity":       0.10,
            "alternative":  0.05,
            "foreign_bond": 0.50,
            "domestic_bond": 0.25,
            "cash":         0.10,
        },
    },
}

# ── 카테고리(프리셋 weight 키) → 실제 티커 매핑 ─────
# 모멘텀 스코어링이 카테고리 내에서 티커별 비중을 결정
CATEGORY_TO_TICKERS: dict[str, list[str]] = {
    "equity":       ["SPY", "IWM", "EFA", "EEM", "EWY"],
    "alternative":  ["VNQ", "DBC", "GLD"],
    "foreign_bond": ["AGG", "IEF", "TLT", "TIP"],
    "domestic_bond": ["SHY"],
    "cash":         ["SHY"],
}


# ══════════════════════════════════════════════════════
# 3. 조회 함수
# ══════════════════════════════════════════════════════

def get_all_tickers() -> list[str]:
    """등록된 모든 자산군의 Yahoo Finance 티커 목록."""
    return [v["ticker"] for v in ASSET_CLASSES.values()]


def get_ticker_to_class_map() -> dict[str, str]:
    """티커 → 자산군 키 매핑.

    예: {"SPY": "us_large", "AGG": "us_bond_agg", ...}
    """
    return {v["ticker"]: k for k, v in ASSET_CLASSES.items()}


def get_class_to_ticker_map() -> dict[str, str]:
    """자산군 키 → 티커 매핑.

    예: {"us_large": "SPY", "us_bond_agg": "AGG", ...}
    """
    return {k: v["ticker"] for k, v in ASSET_CLASSES.items()}


def get_safe_haven_tickers() -> list[str]:
    """safe_haven=True 인 자산군의 티커 목록."""
    return [v["ticker"] for v in ASSET_CLASSES.values() if v.get("safe_haven")]


def get_tickers_by_category(category: str) -> list[str]:
    """특정 카테고리의 자산군 키 목록 → 대응하는 티커 목록.

    Args:
        category: "equity", "bond", "real_asset", "cash"
    """
    keys = CATEGORY_GROUPS.get(category, [])
    return [ASSET_CLASSES[k]["ticker"] for k in keys if k in ASSET_CLASSES]


def get_asset_class_info(key: str) -> dict[str, Any] | None:
    """자산군 키로 메타 정보를 반환. 없으면 None."""
    return ASSET_CLASSES.get(key)


def get_asset_class_by_ticker(ticker: str) -> dict[str, Any] | None:
    """티커로 자산군 메타 정보를 반환. 없으면 None."""
    mapping = get_ticker_to_class_map()
    key = mapping.get(ticker)
    if key is None:
        return None
    info = ASSET_CLASSES[key].copy()
    info["key"] = key
    return info


# ── 프리셋 관련 ───────────────────────────────────

def get_preset(name: str) -> dict[str, Any]:
    """프리셋 이름으로 포트폴리오 설정을 반환. 없으면 balanced 반환."""
    return PORTFOLIO_PRESETS.get(name, PORTFOLIO_PRESETS["balanced"])


def get_preset_names() -> list[str]:
    """프리셋 키 목록."""
    return list(PORTFOLIO_PRESETS.keys())


def get_all_presets_summary() -> list[dict[str, Any]]:
    """프론트엔드 UI용 프리셋 요약 목록.

    Returns:
        [{"key": "growth", "label": "성장형", "icon": "🚀", "risk_level": 5,
          "desc": "...", "weights": {...}}, ...]
    """
    result = []
    for key, preset in PORTFOLIO_PRESETS.items():
        result.append({
            "key": key,
            "label": preset["label"],
            "icon": preset["icon"],
            "risk_level": preset["risk_level"],
            "desc": preset["desc"],
            "weights": preset["weights"],
        })
    return result


def validate_preset_weights(preset_name: str) -> bool:
    """프리셋의 가중치 합이 1.0인지 검증."""
    preset = PORTFOLIO_PRESETS.get(preset_name)
    if preset is None:
        return False
    total = sum(preset["weights"].values())
    return abs(total - 1.0) < 1e-9


# ══════════════════════════════════════════════════════
# 4. CLI — 직접 실행 시 레지스트리 요약 출력
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("자산군 레지스트리 (AssetClassRegistry)")
    print("=" * 60)

    print(f"\n총 {len(ASSET_CLASSES)}개 자산군:")
    for key, info in ASSET_CLASSES.items():
        haven = " ★" if info.get("safe_haven") else ""
        indiv = " [개별종목]" if info.get("has_individual_stocks") else ""
        print(f"  {key:20s}  {info['ticker']:5s}  {info['category']:12s}  {info['label']}{haven}{indiv}")

    print(f"\n카테고리 그룹:")
    for cat, keys in CATEGORY_GROUPS.items():
        tickers = [ASSET_CLASSES[k]["ticker"] for k in keys]
        print(f"  {cat:12s}: {', '.join(tickers)}")

    print(f"\n벤치마크 (60/40): {BENCHMARK_WEIGHTS}")

    print(f"\n포트폴리오 프리셋 ({len(PORTFOLIO_PRESETS)}개):")
    for key, preset in PORTFOLIO_PRESETS.items():
        total = sum(preset["weights"].values())
        valid = "✓" if abs(total - 1.0) < 1e-9 else f"✗ ({total:.2f})"
        print(f"  {preset['icon']} {preset['label']:8s} (risk {preset['risk_level']})  합계={valid}")
        for cat, w in preset["weights"].items():
            bar = "█" * int(w * 40)
            print(f"    {cat:14s}: {w:5.1%}  {bar}")

    print(f"\n안전자산 티커: {get_safe_haven_tickers()}")
    print(f"현금등가 티커: {CASH_TICKER}")
