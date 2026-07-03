# 2026-07-03 (4차) 라이트 테마 · 비교 액션 · 미리보기 · 푸터 배너

## 1. 체크리스트 헤더
- "Before sending (both required, in any order)" → **"Checklist before sending"**.

## 2-a. 라이트 테마 강제
- OS 다크모드와 무관하게 항상 밝은 UI: `_apply_light_theme()` — Fusion 스타일 +
  밝은 QPalette(창 #f5f6f8, 입력 흰색, 하이라이트 네이비). Qt 6.8+에서는
  `styleHints().setColorScheme(Light)`도 시도(구버전은 무시).
- 참고: 창 제목표시줄 색은 OS가 관리하므로 어두울 수 있음.

## 2-b. 비교 다이얼로그 액션
New sign-ups / Departed 목록이 읽기 전용 텍스트 → **체크박스 리스트**(+ Select
all)로 변경. 액션 버튼:

- **New sign-ups → "Add checked to the saved group"**: 체크한 신규 구독자를
  저장 그룹(`Korea contacts`)에 업서트(그룹 없으면 생성). 벌크 API 사용.
- **Departed → "Add checked to the current list (CSV)"**: 체크한 탈퇴자를 현재
  로드된 리스트에 다시 추가(MailerLite의 name/company 필드도 가져옴), 테이블
  갱신 후 **병합된 리스트를 새 CSV로 저장**하는 다이얼로그 표시(utf-8-sig,
  Excel 호환). 중복은 자동 스킵.
- **Departed → "Delete checked from MailerLite…"**: 기존 전체 삭제에서 **체크한
  항목만** 삭제로 변경(여전히 active만, opt-out 기록 보존).
- 한 번에 한 가지 액션을 수행하고 다이얼로그가 닫힘. 여러 액션이 필요하면
  Compare를 다시 실행.
- 내부: `GroupComparison.departed`가 3-튜플 → dict(email/status/id/name/company)
  로 변경. `service.add_to_main_group()`, `content.save_subscribers()` 추가.

## 3. Markdown 미리보기
- MD 파일 행에 **Preview** 버튼. 실제 발송과 동일한 HTML(제목 헤더, PDF 버튼,
  푸터 배너 포함)을 임시 파일로 렌더해 기본 브라우저로 엶.
- Unsubscribe 링크는 `{$unsubscribe}` 플레이스홀더 그대로(발송 시 MailerLite가
  수신자별로 치환) — 상태바에 안내 문구 표시.

## 4. 푸터 배너 (Unsubscribe 버튼 + SNS 링크)
- 이메일 본문 맨 아래(기존 회색 푸터 위)에 **네이비(#1f3864) 배너** 추가:
  - SNS 텍스트 링크 줄 (가운데 정렬, `·` 구분)
  - 흰색 **Unsubscribe 버튼** (MailerLite 개인화 링크)
- SNS URL은 임의로 넣지 않고 **Settings의 "Footer banner links"**에서 입력:
  Website / Facebook / X / Instagram / YouTube / LinkedIn (모두 선택 사항,
  채운 것만 배너에 표시. 하나도 없으면 Unsubscribe 버튼만).
- `config.json`의 `social_links`에 저장. `CampaignDraft.social_links`로 전달.
- 디자인은 기존 헤더 밴드와 동일한 네이비 톤 — Preview 버튼으로 확인 후 색/
  배치 조정 요청 가능.

## 5. (추가 요청) 비교 기준 그룹 이름 변경 + 그룹 보존 동기화

- 비교·동기화 대상 그룹 이름이 하드코딩("Korea contacts") → **Settings의
  "Subscriber group" 필드**로 이동. 기본값 **"ETO Korea Newsletter
  Subscribers"** (`config.json`의 `group_name`).
- Compare 흐름: ① 설정된 그룹 이름으로 먼저 검색(대소문자·공백 무시) →
  ② 없으면 계정 전체 폴백(다이얼로그에 그룹 이름과 Settings 확인 안내 표시).
- **발송 후 그룹 갱신 방식 변경**: 기존 "삭제 후 재생성" → **멤버십 동기화**
  (`_sync_main_group_membership`): 발송 대상 업서트 + 발송에 없던 기존 멤버는
  그룹에서만 해제(`DELETE /subscribers/{id}/groups/{gid}`). 이제 실제 운영
  그룹을 대상으로 하므로 그룹 ID·대시보드 연결(가입 폼 등)이 보존됨.
- 내부 작업용 그룹 이름도 중립적으로 변경: "ETO Newsletter (current send)" /
  "(test)". (MailerLite에 남아 있는 옛 "Korea contacts (current send)/(test)"
  그룹은 수동 삭제 가능.)
- 비교 다이얼로그의 "Add checked to the saved group" 버튼이 실제 그룹 이름을
  표시.

## 검증
- `py_compile` 전 모듈 + import 통과.
- 유닛: 배너 렌더(빈 링크 스킵, 링크 없음 → 버튼만), CSV 저장/로드 왕복(한글),
  departed dict 흐름(비교→선택 삭제→그룹 추가) 통과.
- offscreen GUI 스모크: MainWindow, ComparisonDialog(전체 체크/빈 비교),
  SettingsDialog(6개 링크 필드) 생성 정상.
