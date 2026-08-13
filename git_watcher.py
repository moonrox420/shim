#!/usr/bin/env python3
import os
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

class GitAutoCommitHandler(FileSystemEventHandler):
    def __init__(self, repo_path: Path, debounce_seconds: float = 2.0):
        super().__init__()
        self.repo_path = repo_path.resolve()
        self.debounce_seconds = debounce_seconds
        self.last_triggered: float = 0.0

        if not (self.repo_path / ".git").exists():
            raise ValueError(f"Target '{self.repo_path}' is not a Git repo.")

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            args, cwd=str(self.repo_path),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=True
        )

    def trigger_commit(self, relative_path: str) -> None:
        try:
            status = self._run_git(["git", "status", "--porcelain"])
            if not status.stdout.strip():
                return  # No changes, skip empty commits

            print(f"[{datetime.now().strftime('%H:%M:%S')}] Auto-staging: {relative_path}")
            self._run_git(["git", "add", "."])
            
            commit_msg = f"auto-commit: updates saved in {relative_path}"
            result = self._run_git(["git", "commit", "-m", commit_msg])
            print(f"[COMMIT SUCCESS] {result.stdout.strip().splitlines()[0]}")
        except subprocess.CalledProcessError as err:
            print(f"[ERROR] Git failed: {err.stderr.strip()}", file=sys.stderr)

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        # Skip tracking git metadata folder or temp files
        ignored = [".git/", "__pycache__", ".tmp", "git_watcher.py"]
        if any(pattern in event.src_path for pattern in ignored):
            return

        current_time = time.time()
        relative_path = os.path.relpath(event.src_path, start=self.repo_path)

        if (current_time - self.last_triggered) > self.debounce_seconds:
            self.last_triggered = current_time
            time.sleep(0.5)  # Let disk finish writing
            self.trigger_commit(relative_path)

def start_watcher(path_to_watch: str) -> None:
    watch_path = Path(path_to_watch).resolve()
    print(f"[*] Starting local-kernel watcher on: {watch_path}")
    
    event_handler = GitAutoCommitHandler(repo_path=watch_path)
    observer = Observer()
    observer.schedule(event_handler, path=str(watch_path), recursive=True)
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Stopping Watcher safely...")
        observer.stop()
    observer.join()

if __name__ == "__main__":
    target_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    start_watcher(target_dir)
