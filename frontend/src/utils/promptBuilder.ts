/**
 * 프롬프트 빌더 — CombinedGapResult → LLM 프롬프트 문자열 3종 생성
 *
 * 기존 App.tsx의 generateAIPrompt() 패턴(하드코딩 템플릿 + 런타임 데이터 삽입)을
 * 확장하여, 포트폴리오 비교분석용 3단계 체인 프롬프트를 자동 조립합니다.
 *
 *   Prompt 1: buildGapPrompt       — 정량 GAP + 섹터/테마 분석 요청
 *   Prompt 2: buildSemanticPrompt  — 종목별 시멘틱 중복도 분석
 *   Prompt 3: buildActionPlanPrompt — 리밸런싱 실행 계획 수립
 *   Full:     buildFullPrompt      — 1+2+3 합산
 *
 * 의존성: types/index.ts, utils/sectorMap.ts
 */

import type {
    CombinedGapResult,
    PortfolioGap,
    MatchedStock,
    MissingTarget,
    CategoryGap,
    StockPosition,
} from '../types';
import { isKrETF, getGlobalLabel, GLOBAL_ETF_CATEGORY_MAP } from './sectorMap';

// 프롬프트 길이 제한 — 종목 수가 많을 때 자동 요약
const MAX_TABLE_ROWS = 25;         // 매칭 테이블 최대 행 수
const MAX_LIST_ITEMS = 20;         // 과잉보유/미보유 리스트 최대 항목
const MAX_CATEGORY_ROWS = 15;     // 카테고리 괴리 테이블 최대 행
const FULL_PROMPT_WARN_LENGTH = 12_000; // 전체 복사 시 경고 문자 수

// ═══════════════════════════════════════════════════
//  포맷팅 헬퍼
// ═══════════════════════════════════════════════════

/** 비중을 퍼센트(%) 문자열로 변환 (소수 2자리) */
function pct(value: number): string {
    return `${(value * 100).toFixed(2)}%`;
}

/** 금액을 한국식 표기 (억/만원)로 변환 */
function formatKrw(value: number): string {
    const abs = Math.abs(value);
    const sign = value < 0 ? '-' : '';

    if (abs >= 1_0000_0000) {
        const eok = (abs / 1_0000_0000).toFixed(1);
        return `${sign}${eok}억원`;
    }
    if (abs >= 1_0000) {
        const man = (abs / 1_0000).toFixed(0);
        return `${sign}${man}만원`;
    }
    return `${sign}${abs.toLocaleString()}원`;
}

/** 모멘텀 스코어를 소수 2자리 문자열로 변환 */
function fmtScore(score: number | undefined): string {
    return score != null ? score.toFixed(2) : '-';
}

/** 액션 한글 라벨 */
function actionLabel(action: 'hold' | 'increase' | 'decrease'): string {
    switch (action) {
        case 'hold': return '유지';
        case 'increase': return '▲ 비중확대';
        case 'decrease': return '▼ 비중축소';
    }
}

/** ETF 플래그가 있는 종목명 생성 */
function nameWithFlag(ticker: string, name: string, mode: 'kr' | 'global'): string {
    if (mode === 'kr' && isKrETF(ticker)) {
        return `${name} [ETF]`;
    }
    return name;
}

/** 과잉보유 종목 목록을 프롬프트 문자열로 변환 */
function overHoldingsBlock(overHoldings: StockPosition[]): string {
    if (overHoldings.length === 0) return '  (없음)\n';
    const display = overHoldings.slice(0, MAX_LIST_ITEMS);
    const lines = display
        .map((p) => {
            const evalStr = p.evalAmount ? ` / 평가 ${formatKrw(p.evalAmount)}` : '';
            return `  - ${p.name} (${p.currency})${evalStr}`;
        });
    if (overHoldings.length > MAX_LIST_ITEMS) {
        lines.push(`  ... 외 ${overHoldings.length - MAX_LIST_ITEMS}개 (요약 모드)`);
    }
    return lines.join('\n') + '\n';
}

/** 미보유 타겟 목록을 프롬프트 문자열로 변환 */
function missingTargetsBlock(targets: MissingTarget[]): string {
    if (targets.length === 0) return '  (없음)\n';
    const display = targets.slice(0, MAX_LIST_ITEMS);
    const lines = display
        .map((t) => {
            const sectorStr = t.sector ? ` [${t.sector}]` : '';
            const scoreStr = t.score != null ? `, 스코어 ${fmtScore(t.score)}` : '';
            return `  - ${t.name}${sectorStr}: 타겟비중 ${pct(t.weight)}${scoreStr}`;
        });
    if (targets.length > MAX_LIST_ITEMS) {
        lines.push(`  ... 외 ${targets.length - MAX_LIST_ITEMS}개 (요약 모드)`);
    }
    return lines.join('\n') + '\n';
}

/** 카테고리 괴리 테이블 (마크다운 형식) */
function categoryGapTable(gaps: Record<string, CategoryGap>): string {
    const sorted = Object.entries(gaps)
        .sort(([, a], [, b]) => Math.abs(b.gap) - Math.abs(a.gap));

    if (sorted.length === 0) return '  (데이터 없음)\n';

    const display = sorted.slice(0, MAX_CATEGORY_ROWS);

    const lines = [
        '  | 카테고리 | 실제비중 | 타겟비중 | 괴리 |',
        '  |---------|---------|---------|------|',
    ];
    for (const [sector, gap] of display) {
        const gapSign = gap.gap > 0 ? '+' : '';
        lines.push(`  | ${sector} | ${pct(gap.actual)} | ${pct(gap.target)} | ${gapSign}${pct(gap.gap)} |`);
    }
    if (sorted.length > MAX_CATEGORY_ROWS) {
        lines.push(`  | ... 외 ${sorted.length - MAX_CATEGORY_ROWS}개 | | | |`);
    }
    return lines.join('\n') + '\n';
}

/** 매칭 종목 테이블 (마크다운 형식) */
function matchedTable(matched: MatchedStock[], mode: 'kr' | 'global'): string {
    if (matched.length === 0) return '  (매칭된 종목 없음)\n';

    const header = mode === 'kr'
        ? '  | 종목명 | 섹터 | 보유비중 | 타겟비중 | 괴리 | 스코어 | 조치 | 조정금액 |'
        : '  | ETF명 | 카테고리 | 보유비중 | 타겟비중 | 괴리 | 스코어 | 조치 | 조정금액 |';

    const divider = mode === 'kr'
        ? '  |-------|------|---------|---------|------|--------|------|---------|'
        : '  |-------|---------|---------|---------|------|--------|------|---------|';

    const sorted = [...matched].sort((a, b) => Math.abs(b.weightGap) - Math.abs(a.weightGap));
    const display = sorted.slice(0, MAX_TABLE_ROWS);

    const rows = display
        .map((m) => {
            const gapSign = m.weightGap > 0 ? '+' : '';
            const nm = nameWithFlag(m.ticker, m.name, mode);
            return `  | ${nm} | ${m.sector ?? '미분류'} | ${pct(m.actualWeight)} | ${pct(m.targetWeight)} | ${gapSign}${pct(m.weightGap)} | ${fmtScore(m.momentumScore)} | ${actionLabel(m.action)} | ${formatKrw(m.adjustAmount)} |`;
        });

    const result = [header, divider, ...rows];
    if (matched.length > MAX_TABLE_ROWS) {
        result.push(`  | ... 외 ${matched.length - MAX_TABLE_ROWS}개 (요약 모드) | | | | | | | |`);
    }
    return result.join('\n') + '\n';
}

// ═══════════════════════════════════════════════════
//  섹션 빌더 — 국내/글로벌 GAP 블록 조립
// ═══════════════════════════════════════════════════

/** 단일 PortfolioGap을 프롬프트 섹션으로 변환 (Prompt 1용) */
function buildGapSection(gap: PortfolioGap): string {
    const flag = gap.mode === 'kr' ? '🇰🇷 국내' : '🌍 글로벌';
    const label = gap.targetLabel;

    const lines: string[] = [];
    lines.push(`\n[${flag} — ${label}]`);

    // 매칭 종목 테이블
    lines.push(`\n● 매칭 종목 (보유 ∩ 타겟): ${gap.matched.length}개`);
    lines.push(matchedTable(gap.matched, gap.mode));

    // 과잉보유
    lines.push(`● 과잉보유 (보유 O / 타겟 X): ${gap.overHoldings.length}개`);
    lines.push(overHoldingsBlock(gap.overHoldings));

    // 미보유 타겟
    lines.push(`● 미보유 타겟 (보유 X / 타겟 O): ${gap.missingTargets.length}개`);
    lines.push(missingTargetsBlock(gap.missingTargets));

    // 카테고리 배분 괴리
    const catLabel = gap.mode === 'kr' ? 'WICS 섹터' : '자산군 카테고리';
    lines.push(`● ${catLabel} 배분 괴리`);
    lines.push(categoryGapTable(gap.categoryGaps));

    return lines.join('\n');
}

/** 매칭 종목 중 시멘틱 분석이 필요한 주요 쌍을 추출 (Prompt 2용) */
function extractSemanticPairs(gap: PortfolioGap): string {
    const lines: string[] = [];
    const flag = gap.mode === 'kr' ? '🇰🇷 국내' : '🌍 글로벌';

    // 1) 과잉보유 vs 미보유 타겟: 같은 섹터에 속하는 쌍 → 대체 가능성 분석
    const overBySector: Record<string, StockPosition[]> = {};
    for (const pos of gap.overHoldings) {
        // 과잉보유는 섹터 정보가 StockPosition에 없으므로 이름으로 묶음
        const key = '미분류'; // 프롬프트에서 LLM이 판단하도록 위임
        overBySector[key] = overBySector[key] ?? [];
        overBySector[key].push(pos);
    }

    if (gap.overHoldings.length > 0 && gap.missingTargets.length > 0) {
        lines.push(`\n[${flag} — 대체 가능성 분석 대상]`);
        lines.push('');
        lines.push('아래 "현재 보유 중 (과잉보유)" 종목과 "타겟 추천 (미보유)" 종목 사이의');
        lines.push('사업영역·섹터·테마 중복도를 평가해 주세요.\n');

        // 과잉보유 종목 나열
        lines.push('현재 보유 중 (타겟에 없음):');
        for (const pos of gap.overHoldings.slice(0, 15)) {
            lines.push(`  - ${pos.name} (${pos.currency})`);
        }
        if (gap.overHoldings.length > 15) {
            lines.push(`  ... 외 ${gap.overHoldings.length - 15}개`);
        }

        lines.push('');
        lines.push('타겟 추천 (현재 미보유):');
        for (const t of gap.missingTargets.slice(0, 15)) {
            const sectorStr = t.sector ? ` [${t.sector}]` : '';
            lines.push(`  - ${t.name}${sectorStr} (타겟비중 ${pct(t.weight)})`);
        }
        if (gap.missingTargets.length > 15) {
            lines.push(`  ... 외 ${gap.missingTargets.length - 15}개`);
        }
        lines.push('');
    }

    // 2) 매칭 종목 중 action이 decrease인 것 → "왜 비중을 줄여야 하는가" 시멘틱 판단
    const decreaseStocks = gap.matched.filter((m) => m.action === 'decrease');
    if (decreaseStocks.length > 0) {
        lines.push(`[${flag} — 비중 과다 종목 심층 분석]`);
        lines.push('');
        lines.push('아래 종목들은 타겟 대비 비중이 과다합니다.');
        lines.push('같은 섹터/테마 내 다른 타겟 종목과의 중복 노출 여부를 판단해 주세요.\n');
        for (const m of decreaseStocks) {
            const nm = nameWithFlag(m.ticker, m.name, gap.mode);
            lines.push(`  - ${nm} [${m.sector ?? '미분류'}]: 보유 ${pct(m.actualWeight)} vs 타겟 ${pct(m.targetWeight)} (초과 ${pct(m.weightGap)})`);
        }
        lines.push('');
    }

    return lines.join('\n');
}

// ═══════════════════════════════════════════════════
//  메인 빌더 함수 — 외부 API
// ═══════════════════════════════════════════════════

/**
 * Prompt 1: 정량 GAP 분석 + 섹터/테마 분석 요청
 *
 * 시스템이 자동으로 계산한 정량 데이터를 제공하고,
 * LLM에게 섹터/테마 관점의 정성적 분석을 요청합니다.
 */
export function buildGapPrompt(gap: CombinedGapResult): string {
    const sections: string[] = [];

    sections.push(`당신은 CFA·CAIA 자격을 보유한 포트폴리오 리밸런싱 전문가입니다.
아래의 정량 GAP 데이터를 분석하고, 섹터/테마 관점에서 포트폴리오 리밸런싱 방향을 제시해주세요.

[분석 대상 포트폴리오]
- 총 자본금: ${formatKrw(gap.totalCapital)}
- 환율 (USD/KRW): ${gap.usdToKrw.toLocaleString()}원`);

    // ── 국내 GAP 섹션 ──
    if (gap.kr) {
        sections.push(buildGapSection(gap.kr));
    }

    // ── 글로벌 GAP 섹션 ──
    if (gap.global) {
        sections.push(buildGapSection(gap.global));
    }

    // ── 분석 요청사항 ──
    sections.push(`
[분석 요청]
1. 과잉보유 종목 중 타겟과 **섹터/테마가 겹치는** 것이 있는지 판단해 주세요.
   - 예: "삼성전자(반도체)를 보유 중이고 타겟에 SK하이닉스(반도체)가 있다면, 반도체 섹터 노출은 이미 확보된 것"
2. 미보유 타겟 중 기존 보유종목으로 **대체 노출이 가능한** 것이 있는지 분석해 주세요.
3. 카테고리/섹터 배분 괴리에서 **전략적으로 조정이 필요한 영역**을 우선순위와 함께 제안해 주세요.
4. 동일 섹터 내 종목 쏠림(집중도)으로 인한 **동조화 리스크**가 있는지 평가해 주세요.`);

    // ── 국내+글로벌 교차분석 가이드 ──
    if (gap.kr && gap.global) {
        sections.push(`
5. 국내 포트폴리오와 글로벌 자산배분 간 **교차 분석**도 수행해 주세요.
   - 예: "국내 반도체 과잉 보유 + 글로벌 EEM(신흥국) 미보유 → 신흥국 반도체 노출이 부족할 수 있음"
   - 예: "국내 2차전지 과다 + 글로벌에서 원자재(DBC) 보유 → 원자재 슈퍼사이클 테마 중복 가능성"`);
    }

    return sections.join('\n');
}

/**
 * Prompt 2: 종목별 시멘틱 중복도 분석
 *
 * 과잉보유 ↔ 미보유 타겟 사이의 사업영역·테마 중복 가능성과
 * 비중 과다 종목의 대체 가능 여부를 LLM에 분석 의뢰합니다.
 */
export function buildSemanticPrompt(gap: CombinedGapResult): string {
    const sections: string[] = [];

    sections.push(`당신은 종목 분석 전문가입니다. 아래 종목 쌍들의 **사업영역·섹터·테마 중복도**를 분석해 주세요.

[분석 기준]
- 중복도: 0%(완전 이질) ~ 100%(사실상 동일)
- 50% 이상이면 "대체 노출 가능" → 하나를 보유하면 다른 하나의 편입 우선순위 낮춤
- 30% 미만이면 "별도 편입 필요" → 두 종목 모두 보유 권장

[참고] 아래 섹터 정보는 WICS 업종분류(국내) 또는 자산군 분류(글로벌)입니다.
       "반도체 ≈ AI 인프라", "2차전지 ≈ EV 밸류체인" 등 유연한 시멘틱 추론을 해 주세요.`);

    // 국내 시멘틱 분석
    if (gap.kr) {
        const krSection = extractSemanticPairs(gap.kr);
        if (krSection.trim()) sections.push(krSection);
    }

    // 글로벌 시멘틱 분석
    if (gap.global) {
        const globalSection = extractSemanticPairs(gap.global);
        if (globalSection.trim()) sections.push(globalSection);
    }

    // 국내↔글로벌 교차 시멘틱 분석
    if (gap.kr && gap.global) {
        sections.push(buildCrossSemanticSection(gap));
    }

    sections.push(`
[응답 형식]
각 주요 종목 쌍에 대해 아래 형식으로 답변해 주세요:

| 종목 A | 종목 B | 중복도 | 판단 | 근거 (1줄) |
|--------|--------|--------|------|-----------|
| 삼성전자 | SK하이닉스 | 75% | 대체 가능 | 둘 다 메모리 반도체 핵심기업 |`);

    return sections.join('\n');
}

/** 국내 ↔ 글로벌 교차 시멘틱 분석 섹션 */
function buildCrossSemanticSection(gap: CombinedGapResult): string {
    const lines: string[] = [];

    lines.push('\n[🔀 국내 ↔ 글로벌 교차 시멘틱 분석]');
    lines.push('');
    lines.push('국내 보유종목과 글로벌 ETF 사이의 테마 중복 가능성을 분석해 주세요.');
    lines.push('(예: 국내 반도체 보유 → 글로벌 SPY 내 반도체 비중으로 간접 노출 가능)\n');

    // 국내 매칭+과잉보유 종목의 섹터 분포
    const krSectors = new Set<string>();
    if (gap.kr) {
        for (const m of gap.kr.matched) {
            if (m.sector) krSectors.add(m.sector);
        }
    }

    // 글로벌 매칭+미보유 ETF의 카테고리 요약
    if (gap.global) {
        lines.push('국내 보유 섹터: ' + (krSectors.size > 0 ? [...krSectors].join(', ') : '(정보없음)'));
        lines.push('글로벌 포트폴리오:');
        for (const m of gap.global.matched) {
            const globalLabel = getGlobalLabel(m.ticker);
            const desc = GLOBAL_ETF_CATEGORY_MAP[m.ticker]?.description ?? '';
            lines.push(`  - ${m.ticker} (${globalLabel}${desc ? ' / ' + desc : ''}): 보유 ${pct(m.actualWeight)}, 타겟 ${pct(m.targetWeight)}`);
        }
        for (const t of gap.global.missingTargets) {
            const globalLabel = getGlobalLabel(t.ticker);
            const desc = GLOBAL_ETF_CATEGORY_MAP[t.ticker]?.description ?? '';
            lines.push(`  - ${t.ticker} (${globalLabel}${desc ? ' / ' + desc : ''}) [미보유]: 타겟 ${pct(t.weight)}`);
        }
        lines.push('');
    }

    return lines.join('\n');
}

/**
 * Prompt 3: 리밸런싱 실행 계획 수립
 *
 * GAP 분석과 시멘틱 분석 결과를 종합하여,
 * 구체적인 매수/매도 주문 목록을 생성하도록 LLM에 요청합니다.
 *
 * 이 프롬프트는 Prompt 1·2의 LLM 응답을 사용자가 붙여넣은 후에
 * 이어서 사용하는 것을 권장합니다 (컨텍스트 체인).
 */
export function buildActionPlanPrompt(gap: CombinedGapResult): string {
    const sections: string[] = [];

    sections.push(`당신은 리밸런싱 실행 전문가입니다.
앞선 분석 결과(정량 GAP + 시멘틱 분석)를 종합하여, 구체적인 리밸런싱 실행 계획을 수립해 주세요.

[제약 조건]
- 총 자본금: ${formatKrw(gap.totalCapital)}
- 환율 (USD/KRW): ${gap.usdToKrw.toLocaleString()}원
- 거래 비용 고려: 매매 수수료 약 0.015%, 슬리피지 약 0.1%
- 최소 거래 금액: 국내 1주 단위, 해외 1주 단위`);

    // 현재 조정 필요한 종목 요약
    const allActions: string[] = [];

    if (gap.kr) {
        const krDecreases = gap.kr.matched.filter((m) => m.action === 'decrease');
        const krIncreases = gap.kr.matched.filter((m) => m.action === 'increase');

        if (krDecreases.length > 0) {
            allActions.push('\n[국내 — 비중 축소 후보]');
            for (const m of krDecreases) {
                allActions.push(`  - ${m.name} [${m.sector ?? '미분류'}]: 현재 ${pct(m.actualWeight)} → 타겟 ${pct(m.targetWeight)} (${formatKrw(m.adjustAmount)} 축소 필요)`);
            }
        }
        if (krIncreases.length > 0) {
            allActions.push('\n[국내 — 비중 확대 후보]');
            for (const m of krIncreases) {
                allActions.push(`  - ${m.name} [${m.sector ?? '미분류'}]: 현재 ${pct(m.actualWeight)} → 타겟 ${pct(m.targetWeight)} (${formatKrw(m.adjustAmount)} 확대 필요)`);
            }
        }
        if (gap.kr.missingTargets.length > 0) {
            allActions.push('\n[국내 — 신규 편입 후보]');
            for (const t of gap.kr.missingTargets) {
                const allocAmount = t.weight * gap.totalCapital;
                allActions.push(`  - ${t.name} [${t.sector ?? '미분류'}]: 타겟 ${pct(t.weight)} ≈ ${formatKrw(allocAmount)}`);
            }
        }
        if (gap.kr.overHoldings.length > 0) {
            allActions.push('\n[국내 — 전량 매도 검토 대상]');
            for (const p of gap.kr.overHoldings) {
                const evalStr = p.evalAmount ? ` (평가 ${formatKrw(p.evalAmount)})` : '';
                allActions.push(`  - ${p.name}${evalStr}`);
            }
        }
    }

    if (gap.global) {
        const glDecreases = gap.global.matched.filter((m) => m.action === 'decrease');
        const glIncreases = gap.global.matched.filter((m) => m.action === 'increase');

        if (glDecreases.length > 0) {
            allActions.push('\n[글로벌 — 비중 축소 후보]');
            for (const m of glDecreases) {
                const globalLabel = getGlobalLabel(m.ticker);
                allActions.push(`  - ${m.name} (${globalLabel}): 현재 ${pct(m.actualWeight)} → 타겟 ${pct(m.targetWeight)} (${formatKrw(m.adjustAmount)} 축소 필요)`);
            }
        }
        if (glIncreases.length > 0) {
            allActions.push('\n[글로벌 — 비중 확대 후보]');
            for (const m of glIncreases) {
                const globalLabel = getGlobalLabel(m.ticker);
                allActions.push(`  - ${m.name} (${globalLabel}): 현재 ${pct(m.actualWeight)} → 타겟 ${pct(m.targetWeight)} (${formatKrw(m.adjustAmount)} 확대 필요)`);
            }
        }
        if (gap.global.missingTargets.length > 0) {
            allActions.push('\n[글로벌 — 신규 편입 후보]');
            for (const t of gap.global.missingTargets) {
                const allocAmount = t.weight * gap.totalCapital;
                const globalLabel = getGlobalLabel(t.ticker);
                allActions.push(`  - ${t.name} (${globalLabel}): 타겟 ${pct(t.weight)} ≈ ${formatKrw(allocAmount)}`);
            }
        }
    }

    sections.push(allActions.join('\n'));

    sections.push(`
[실행 계획 요청]

1. **매도 우선 원칙**: 매수 자금 확보를 위해 매도를 먼저 실행합니다.
   아래 순서로 실행 계획을 세워 주세요:
   (1) 전량 매도 대상 (과잉보유 중 대체 노출 불가한 종목)
   (2) 비중 축소 대상 (부분 매도)
   (3) 비중 확대 대상 (추가 매수)
   (4) 신규 편입 대상

2. 각 종목에 대해 아래 형식으로 구체적인 주문 목록을 작성해 주세요:

   | 순서 | 구분 | 종목명 | 매매방향 | 목표비중 | 예상수량 | 예상금액 | 사유 |
   |------|------|--------|---------|---------|---------|---------|------|

3. **시멘틱 분석 반영**: 앞선 시멘틱 분석에서 "대체 노출 가능"으로 판단된 종목은
   편입 우선순위를 낮추고, 그 근거를 사유에 명시해 주세요.

4. **리스크 고려**: 동일 섹터에 리밸런싱 후 30% 이상 집중되지 않도록 주의해 주세요.

5. 최종적으로, 리밸런싱 전후의 **카테고리 배분 비교표**를 함께 제공해 주세요.`);

    return sections.join('\n');
}

/**
 * 3단계 프롬프트를 모두 합산하여 하나의 문자열로 반환합니다.
 *
 * 단일 복사로 LLM에 모든 분석을 한 번에 요청할 때 사용합니다.
 * 프롬프트가 길어질 수 있으므로, 종목 수가 많을 경우 요약 모드가 자동 적용됩니다.
 */
export function buildFullPrompt(gap: CombinedGapResult): string {
    const SEPARATOR = '\n\n' + '═'.repeat(60) + '\n\n';

    const parts = [
        '[ PART 1 / 3 — 정량 GAP 분석 + 섹터·테마 분석 ]',
        buildGapPrompt(gap),
        SEPARATOR,
        '[ PART 2 / 3 — 종목 시멘틱 중복도 분석 ]',
        buildSemanticPrompt(gap),
        SEPARATOR,
        '[ PART 3 / 3 — 리밸런싱 실행 계획 ]',
        buildActionPlanPrompt(gap),
    ];

    const result = parts.join('\n');

    // 프롬프트 길이 경고 주석 추가
    if (result.length > FULL_PROMPT_WARN_LENGTH) {
        const header = `⚠️ 프롬프트 길이: ${result.length.toLocaleString()}자 (종목 수가 많아 일부 항목이 요약되었습니다.\n필요 시 단계별 탭에서 개별 복사하여 사용하세요.)\n\n`;
        return header + result;
    }

    return result;
}

// ═══════════════════════════════════════════════════
//  프롬프트 메타 정보 (UI에서 탭 구성용으로 사용)
// ═══════════════════════════════════════════════════

export interface PromptMeta {
    key: 'gap' | 'semantic' | 'action' | 'full';
    label: string;
    icon: string;
    description: string;
    builder: (gap: CombinedGapResult) => string;
}

/** UI에서 프롬프트 탭을 렌더링할 때 사용하는 메타데이터 */
export const PROMPT_TABS: readonly PromptMeta[] = [
    {
        key: 'gap',
        label: '전체 분석',
        icon: '📊',
        description: '정량 GAP 데이터 + 섹터/테마 분석 요청',
        builder: buildGapPrompt,
    },
    {
        key: 'semantic',
        label: '시멘틱 분석',
        icon: '🔍',
        description: '종목 간 사업영역·테마 중복도 분석',
        builder: buildSemanticPrompt,
    },
    {
        key: 'action',
        label: '실행 계획',
        icon: '📝',
        description: '구체적인 리밸런싱 매수/매도 주문 목록 생성',
        builder: buildActionPlanPrompt,
    },
    {
        key: 'full',
        label: '전체 복사',
        icon: '📋',
        description: '3단계 프롬프트를 합산하여 한 번에 복사',
        builder: buildFullPrompt,
    },
] as const;
