---
name: skill-promote
description: AI_saver 스킬 승격 — 반복되는 작업 습관을 /명령어 skill로 만들거나, 이미 만든 skill을 압축·병합·삭제한다. "스킬로 만들어줘", "명령어로 승격", "skill 후보", "스킬 정리", "스킬 다이어트" 요청 시 사용.
---

# 스킬 승격과 다이어트

description은 매 프롬프트마다 읽힌다 -- 함부로 늘리지 않는다. 자세한 근거·예산·감가상각·
작성 규칙은 애매할 때만 [references/criteria.md](references/criteria.md)를 읽는다.

## 절차

1. 후보를 본다.

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/ai_saver_cli.py" promote --list
   ```

   `ai_saver/promotion.py`의 `PURPOSE` 표에 있는 이름만 후보가 된다. 새 이름을 지어내지
   않는다 -- 표에 없는 낭비 패턴이 새로 보이면 승격 전에 `PURPOSE`에 항목부터 추가한다.

2. 아래 4개를 확인한다. **하나라도 실패하면 만들지 않는다.** 애매하면
   [references/criteria.md](references/criteria.md#5개-조건-상세)를 읽는다.

   - 최근 30일 내 5회 이상 (`--list`가 보여준다)
   - 원인 신호가 같다 (같은 명령으로 묶였는지)
   - 예상 절감 > 상시 비용 -- CLI는 이 판단을 안 한다. 한 줄로 사용자에게 보여주고 구한다
   - description ≤ 500자, 본문 ≤ 60줄 -- `promote` 실행 시 자동으로 강제된다

3. 통과했으면 실제로 만든다.

   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/ai_saver_cli.py" promote <명령 이름>
   ```

   `~/.claude/skills/<이름>/SKILL.md`가 만들어지고, **그 순간 `/`에 뜬다.** 사용자에게
   "`/이름`을 쳐서 자동완성에 뜨는지 확인해보세요"라고 알려준다.

## 다 만든 뒤

- **효과 판정은 자동이다.** 14일 뒤부터 `report`가 "승격한 명령어, 효과가 있었나" 절에
  스스로 판정을 보여준다. 리포트가 지우라면 지우고, 계속 쓰라면 둔다 -- 직접 셀 필요 없다.
  정확한 기준은 [references/criteria.md](references/criteria.md#효과-판정-상세).
- 개인 스킬이 여러 개 쌓이면 [스킬 예산](references/criteria.md#스킬-예산)을 확인한다
  (description 합계 2,000 토큰 이하 -- 넘으면 새로 만들지 말고 기존 스킬에 합친다).
