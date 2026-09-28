import os
import subprocess
import sys


def main() -> int:
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--ignored",
                "--exclude-standard",
                "--full-name",
                "-z",
                "--",
                ":(top)**",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        print("Cannot check source-control safety: Git is unavailable.", file=sys.stderr)
        return 2
    if result.returncode:
        print("Cannot check source-control safety: run this from a Git checkout.", file=sys.stderr)
        return 2
    paths = [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]
    if paths:
        print(
            "Tracked files violate ignore rules. Remove these from the Git index while keeping local data before committing:",
            file=sys.stderr,
        )
        for path in paths:
            print(f"  {path!r}", file=sys.stderr)
        return 1
    print("Source-control safety check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
