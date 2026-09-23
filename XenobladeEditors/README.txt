XENOBLADE EDITORS — Wii / Nintendo 3DS

실행
  Wii 에디터 실행.cmd
  3DS 에디터 실행.cmd
  app/XenobladeEditors.exe를 직접 실행하면 게임 선택 화면이 나타납니다.
  Python 설치 없이 실행됩니다. CMD 실행 시 콘솔은 곧 닫힙니다.

폴더 구성
  app/          독립 실행 파일과 공용 실행 환경 (_internal 폴더 포함)
  resources/    artwork: 모나드 아이콘·커버 / fonts: 문자 매핑
  settings/     에디터별 게임 루트 등 사용자 설정
  source/       앞으로 수정할 에디터 소스 코드 (실행에는 필요 없음)
  docs/         에디터별 상세 사용 설명
  development/  빌드 도구·스크립트·검증 기록 (실행에는 필요 없음)

다른 곳으로 옮길 때
  app, resources, settings와 실행 CMD를 함께 옮기세요.
  EXE만 단독으로 꺼내면 실행 환경과 리소스를 찾을 수 없습니다.
  codex-lab 폴더는 실행에 필요하지 않습니다.
  게임 파일은 포함되지 않습니다. 경로가 바뀌면 게임 루트를 다시 선택하세요.

Wii
  기존 XOREA/files 루트 및 한글 문자 테이블 설정을 이어받았습니다.
  수정 후 [저장·게임에 반영]을 누르면 원본 백업 후 게임과 static.arc_OUT에 반영합니다.
  [수정본 내보내기]는 별도 출력 폴더에 저장합니다.
  기본 한글 테이블: resources/fonts/xbsystem.tbl

3DS
  기존 RomFS 루트 설정을 이어받았습니다.
  기존 기능과 동일하게 [수정본 저장]으로 변경분을 별도 폴더에 내보냅니다.
  A고딕13 폰트를 쓰는 작업의 입력용 매핑:
  resources/fonts/n3ds_agothic13_mapping.json
  필요할 때 [문자 매핑 불러오기]에서 선택하세요.

개발 및 검증
  두 에디터의 공통 런타임을 묶어 중복 설치를 줄였습니다.
  source가 독립 실행형의 개발 원본입니다. 수정 후 development/build.py로 재빌드하세요.
  재빌드에는 Python·Pillow가 필요하며 빌드 도구는 development/build-tools에 있습니다.
  codex-lab의 옛 CMD도 새 독립 실행형을 실행하도록 연결해 두었습니다.
  기존 편집 기능을 포장한 것으로, 이 작업에서 추가적인 게임 실행 검증은 하지 않았습니다.
