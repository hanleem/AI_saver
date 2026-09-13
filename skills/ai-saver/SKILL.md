---
name: ai-saver
description: AI_saver로 Codex 토큰 사용, 낭비 습관, 월간 보고서, 보정, 게이트 상태, 스킬 승격 후보를 분석한다. 사용자가 AI_saver, 토큰 사용 분석, 낭비 분석, 월간 리포트, 사용 습관, 스킬 후보를 요청할 때 사용한다.
---

# AI_saver for Codex

먼저 이 저장소의 `scripts/ai_saver_cli.py` 위치를 찾는다. Codex 기록을 아직 원장에 넣지 않았다면 다음 명령을 실행한다.

```powershell
python scripts/ai_saver_cli.py backfill --source codex
```

사용자가 기술 수치를 원하면 `report -t`, 쉬운 요약을 원하면 `report`를 실행한다. 결과에서 가장 큰 습관 3개와 다음에 복사해 쓸 짧은 프롬프트를 제시한다.

다른 명령은 필요할 때만 실행한다.

- 상태: `python scripts/ai_saver_cli.py status`
- 보정: `python scripts/ai_saver_cli.py calibrate`
- 게이트: `python scripts/ai_saver_cli.py gate on|off`
- 승격 후보: `python scripts/ai_saver_cli.py promote --list`
- Codex 스킬 생성: `python scripts/ai_saver_cli.py promote <이름> --platform codex`

원문 프롬프트를 보고서나 답변에 재현하지 않는다. 원장의 해시·길이와 집계 수치만 사용한다. Codex 스킬은 `$이름`으로 호출한다고 안내한다.
