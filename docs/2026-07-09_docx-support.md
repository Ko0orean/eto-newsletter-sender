# 2026-07-09 뉴스레터 본문 .docx 지원

## 요청
뉴스레터 본문을 Markdown 대신 Word(.docx)로 업로드하고 싶음.

## 구현
- 변환기: **mammoth** (docx → 시맨틱 HTML 전용, 순수 파이썬). Word 스타일
  기반 변환이라 제목(Heading 1/2/…), 굵게/기울임, 목록, 표가 이메일에서
  그대로 살아남음. `requirements.txt`와 `Run ETO Newsletter.bat`의 패키지
  체크에 추가 — 매니저님 노트북은 Update bat 실행 시 자동 설치.
- 파이프라인 리팩터링 (`content.py`):
  - `load_newsletter_body(path)` — 확장자에 따라 .md(markdown 라이브러리) /
    .docx(mammoth) 분기, `(body_html, warnings)` 반환. 그 외 확장자는
    명확한 에러.
  - `build_email_html(body_html, ...)` — 이메일 템플릿 래퍼(헤더/PDF 버튼/
    배너/푸터)를 본문 생성에서 분리. `markdown_to_email_html`은 호환용
    래퍼로 유지.
- `CampaignDraft.markdown` → `body_html`로 변경 (`service._render`는
  `build_email_html` 사용).
- GUI: 파일 선택 필터 "Newsletter files (*.docx *.md *.markdown)", 라벨/
  플레이스홀더 갱신. Preview·발송 모두 동일 로더 사용. 테스트 무효화
  지문(파일 경로+mtime)은 docx에도 그대로 적용됨.

## docx 이미지 주의
mammoth는 Word에 삽입된 이미지를 data URI로 인라인하는데, Gmail·Outlook 등
다수 메일 클라이언트가 data URI 이미지를 차단함. 문서에서 이미지가 감지되면
로드 시 경고 다이얼로그 표시(미리보기·테스트에서 확인 유도, 웹 호스팅+링크
권장). Word 변환 경고(mammoth messages)도 함께 표시.

## 검증
- python-docx로 생성한 실제 docx(제목 2단계, 굵게, 불릿, 표, 한국어)를
  변환해 h1/strong/li/table/한국어 모두 확인.
- .md 경로 회귀 통과, 미지원 확장자 에러 확인, service 렌더 통과,
  offscreen GUI 스모크 통과.
