import sys

# 시작 화면 앱은 실행기(launcher)가 자식 프로세스로 띄운다: 업데이트 뒤 다시 시작·구성요소 설치·실패 시 되돌리기.
# 실행기는 무거운 모듈을 불러오지 않아야 하므로 cli보다 먼저 갈라진다.
if len(sys.argv) > 1 and sys.argv[1] == "app" and "--child" not in sys.argv[2:] and not {"-h", "--help"} & set(sys.argv):
    from .launcher import supervise

    raise SystemExit(supervise(sys.argv[2:]))

from .cli import main  # noqa: E402

raise SystemExit(main())
