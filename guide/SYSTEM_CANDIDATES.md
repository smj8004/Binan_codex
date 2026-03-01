# Candidate Systems (R&D Gate-Oriented)

본 문서는 3개 후보 시스템의 규칙/국면/리스크 템플릿/그리드 범위를 정의한다.
목표는 "전략 수 증가"가 아니라 하드 게이트 통과 가능성 검증이다.

## 공통 검증 설정
- 심볼: `BTC/USDT`, `ETH/USDT` (선택적으로 `SOL/USDT`)
- 기간: `2021-01-01` ~ `2026-01-01`
- Timeframe: `1h` (일관 유지)
- Walk-forward: `train=240d`, `test=60d`, `step=30d`, `top_pct=0.15`, `max_candidates=120`
- Cost stress: `fee_multiplier=1.0/1.5/2.0/3.0`, `latency=0/1/3`, `slippage=mixed`

## Track A: A_beta_hedged_carry_momo
- 목적: 방향성 의존도 완화, BTC 베타 노출 완화(프록시)
- 엔트리/엑싯:
  - `carry:momentum` 계열 사용
  - 모멘텀 + 캐리 결합 점수로 long/short
- 리스크 템플릿: `balanced`
  - 부분익절 트리거 + 트레일 + 시간손절 + 변동성 타겟팅
- 그리드: `config/grids/carry_momentum_narrow.yaml`
  - `momentum_fast`, `momentum_slow`, `carry_period`, `carry_weight` 중심의 소규모 탐색

## Track B: B_regime_switch_trend_range
- 목적: 하나의 룰로 전장 커버 금지, 국면별 전략 분리
- 엔트리/엑싯:
  - 추세 국면: `trend:donchian`
  - 횡보 국면: `meanrev:zscore`
  - 내부 regime 라벨(EMA slope + volatility)로 전략 스위칭
- 리스크 템플릿: `defensive`
  - high-vol 사이징 축소를 강하게 적용
- 그리드: `config/grids/regime_switch_narrow.yaml`
  - slope 임계/룩백, high-vol 사이즈 멀티플라이어 위주

## Track C: C_breakout_atr_risk_template
- 목적: 신호보다 체결/리스크 엔진 영향 검증
- 엔트리/엑싯:
  - `breakout:atr_channel` 기반
  - 지정가 진입 + 타임아웃/시장가 fallback 비용 모델 포함
- 리스크 템플릿: `aggressive`
  - 부분익절 + 트레일 + 시간손절 + vol target 조합
- 그리드: `config/grids/breakout_atr_narrow.yaml`
  - ATR 채널 파라미터 + 손절/익절 폭 소규모 탐색

## 과최적화 방지 원칙
- 파라미터 개수 최소화, 좁은 범위 유지
- Walk-forward OOS 우선, 인샘플 최고점 배제
- 후보 3개만 집중 평가 후 통과 1~2개만 유지
