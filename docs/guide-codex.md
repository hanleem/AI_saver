# AI_saver 시작하기 — Codex 편

프로그래밍을 몰라도 그대로 복사해 쓸 수 있게 설명합니다. Codex에서도 실행 전 확인, 세션 종료 자동 집계, 과거 기록 분석, 월간 리포트, 보정, 스킬 승격을 지원합니다.

## 1. 준비와 내려받기

Python 3.10 이상인지 확인하고 저장소를 내려받습니다.

```powershell
python --version
git clone --branch codex https://github.com/hanleem/AI_saver.git
```

## 2. Codex 훅 연결

`~`는 내 사용자 폴더입니다. Windows에서 `~/.codex/hooks.json`은 보통 `C:\Users\내이름\.codex\hooks.json`입니다. 파일이 없으면 새 텍스트 파일을 만들고 이름을 `hooks.json`으로 정합니다.

아래 두 명령의 `내려받은 경로`를 실제 `AI_saver` 폴더로 바꿉니다. Python 명령이 여러 버전 중 잘못 잡히면 `python` 대신 Python 실행 파일의 전체 경로를 넣습니다.

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python \"내려받은 경로/hooks/codex_on_prompt.py\"",
            "timeout": 5
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python \"내려받은 경로/hooks/codex_on_session_end.py\"",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

저장한 뒤 Codex를 다시 시작합니다. Codex에서 `/hooks`를 열어 두 명령의 정확한 경로를 확인하고 신뢰(Trust)해야 새 훅이 실행됩니다.

## 3. 이미 쌓인 Codex 기록 분석

PowerShell에서 저장소 폴더로 이동한 뒤 실행합니다.

```powershell
cd "내려받은 경로"
python scripts/ai_saver_cli.py backfill --source codex
python scripts/ai_saver_cli.py report -t
```

`-t`는 입력·캐시·출력·추론 토큰 숫자까지 붙입니다. 빼면 초보자용 요약만 나옵니다. 원문 프롬프트는 기본적으로 저장하지 않고 12자 해시와 길이만 원장에 남깁니다.

## 4. 관찰 후 실행 전 확인 켜기

기본값은 조용히 기록만 하는 관찰 모드입니다. 2주 정도 사용하고 다음을 실행합니다.

```powershell
python scripts/ai_saver_cli.py calibrate
python scripts/ai_saver_cli.py gate on
```

끄려면 `python scripts/ai_saver_cli.py gate off`를 실행합니다.

## 5. 반복 습관을 Codex 스킬로 승격

```powershell
python scripts/ai_saver_cli.py promote --list
python scripts/ai_saver_cli.py promote focus-file --platform codex
```

Codex에서는 `/focus-file` 대신 `$focus-file`로 스킬을 명시적으로 부릅니다. AI_saver 자체의 월간 분석 스킬을 설치했다면 `$ai-saver`라고 입력합니다.

## 저장 위치와 개인정보

- 원장과 리포트: `~/.codex/ai-saver/` (Claude용 기록과 분리)
- Codex 개인 스킬: `~/.codex/skills/`
- 프롬프트 원문은 기본적으로 저장하지 않습니다.
- 인터넷 전송 없이 로컬 파일만 읽고 씁니다.

## 막히면

- 훅이 안 도는 것 같으면 Codex를 다시 시작한 뒤 `/hooks`에서 경로와 신뢰 상태를 확인합니다.
- `python` 버전이 예상과 다르면 Python 실행 파일 전체 경로를 사용합니다.
- `can't open file` 오류가 나면 먼저 `cd "AI_saver의 실제 경로"`로 이동했는지 확인합니다.
- 숫자가 비어 있으면 `backfill --source codex`를 먼저 실행합니다.
