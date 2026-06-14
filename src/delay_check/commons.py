import asyncio
import itertools
import sys
import shutil
import subprocess
import tempfile
import logging
from pathlib import Path


class ToolError(Exception):
    pass


class CommandError(Exception):
    pass


def mk_temp(temp_name: str) -> Path:
    try:
        temp_dir = Path(tempfile.gettempdir()) / temp_name
        temp_dir.mkdir(exist_ok=True)
        logging.debug(f"Created temporary directory: {temp_dir}")
        return temp_dir
    except OSError as e:
        logging.error(f"Failed to create temporary directory {temp_name}: {e}")
        raise


def check_tool(tool_name: str) -> str:
    try:
        tool_path = shutil.which(tool_name)
        if not tool_path:
            error_msg = f"Required tool '{tool_name}' not found in system PATH"
            logging.error(error_msg)
            raise ToolError(error_msg)
        logging.debug(f"Found tool '{tool_name}' at: {tool_path}")
        return tool_path
    except ToolError:
        raise
    except Exception as e:
        logging.error(f"Error checking for tool '{tool_name}': {e}")
        raise ToolError(f"Failed to check for tool '{tool_name}': {e}")


def print_Title(title: str, char_len: int = 0, character: str = "="):
    if char_len == 0:
        char_len = len(title) + int((len(title) / 2))
    print("\n" + character * char_len)
    print(f"{title.upper()}".center(char_len))
    print(character * char_len)


def print_subt(title: str, char_len: int = 0, character: str = "-", center=False):
    if char_len == 0:
        char_len = len(title) + int((len(title) / 2))
    print("\n" + character * char_len)
    if center:
        print(f"{title.upper()}".center(char_len))
    else:
        print(title)
    print(character * char_len)


async def load_spinner(sync_func, *args, message: str = "", **kwargs):
    done = asyncio.Event()

    async def spinner():
        try:
            for c in itertools.cycle("|/-\\"):
                if done.is_set():
                    break

                if message != "":
                    sys.stdout.write(f"\r{message}... {c} ")
                else:
                    sys.stdout.write(f"\r{c} ")
                sys.stdout.flush()
                await asyncio.sleep(0.1)

            if message != "":
                sys.stdout.write("\r" + " " * (len(message) + 10) + "\r")
                sys.stdout.write(f"\r{message}...\n")
            else:
                sys.stdout.write(f"\r")
            sys.stdout.flush()
        except Exception as e:
            logging.debug(f"Spinner error: {e}")

    spinner_task = asyncio.create_task(spinner())

    try:
        result = await asyncio.to_thread(sync_func, *args, **kwargs)
        return result
    except Exception as e:
        logging.error(f"Error in spinner task: {e}")
        raise
    finally:
        done.set()
        try:
            await spinner_task
        except asyncio.CancelledError:
            pass


def run_cmd(cmd: list, showlog=False) -> subprocess.CompletedProcess[str]:
    try:
        kwargs = {
            "stdout": None if showlog else subprocess.PIPE,
            "stderr": None if showlog else subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "ignore"
        }

        logging.debug(f"Executing command: {' '.join(cmd)}")
        result = subprocess.run(cmd, **kwargs)

        if result.returncode != 0:
            error_msg = f"Command failed with exit code {result.returncode}"
            logging.error(error_msg)

            if not showlog:
                if result.stdout:
                    logging.error(f"Command stdout: {result.stdout}")
                if result.stderr:
                    logging.error(f"Command stderr: {result.stderr}")

            raise CommandError(error_msg)

        logging.debug(f"Command completed successfully")
        return result

    except CommandError:
        raise
    except FileNotFoundError as e:
        error_msg = f"Command executable not found: {e}"
        logging.error(error_msg)
        raise CommandError(error_msg)
    except PermissionError as e:
        error_msg = f"Permission denied executing command: {e}"
        logging.error(error_msg)
        raise CommandError(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error executing command: {e}"
        logging.error(error_msg)
        raise CommandError(error_msg)
