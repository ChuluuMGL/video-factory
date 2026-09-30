"""Terminal / JSON views over the same offline Setup session."""

import getpass
import json
import sys

from .onboarding import (QUESTIONS, SessionStore, SetupError, describe,
                         read_input_file, read_json)


def interactive(store, *, read=None, read_reference=None, write=None, next_step=None):
    read = input if read is None else read
    read_reference = getpass.getpass if read_reference is None else read_reference
    write = print if write is None else write
    write("欢迎使用 Video Factory Setup")
    write("本步骤收集配置并生成部署计划，不连接服务器、不安装服务、不调用模型。")
    write("凭据只填写 env:变量名 或 secret:别名；不要输入原始 Key、密码或私钥。")
    write("每一步自动保存；输入 :back 返回前题，:quit 保存退出。")
    override = None
    while True:
        session = store.read()
        result = describe(session)
        if result["status"] == "plan_ready" and override is None:
            write("部署计划已生成。安装、连接、身份验证及任务验收均尚未执行。")
            write(next_step or "下一步在客户主机执行 setup-deploy plan/apply；安装后使用 setup-feishu configure 填连接问题，再用 setup-feishu connect 打开飞书授权与确认向导。")
            return result
        question = override or result["next_question"]
        write("\n[" + question["step"] + "] " + question["question"])
        if question["kind"] == "products":
            write("终端输入：填写包含该数组的 JSON 文件绝对路径；向导读取文件后提交数组。")
        for number, (value, label) in enumerate(question["choices"], 1):
            write(f"  {number}. {label} [{value}]")
        group, key = question["field"].split(".")
        default = session["configuration"][group].get(key, question["default"])
        if default is not None and question["kind"] not in ("products", "reference"):
            write("回车使用：" + str(default))
        try:
            # References are non-secret, but hide input in case a user pastes a Key.
            answer = (read_reference("引用> ") if question["kind"] == "reference"
                      else read("输入> ")).strip()
            if len(answer) > 1024:
                raise SetupError("SETUP_INPUT_TOO_LARGE")
            if answer == ":quit":
                write("进度已保存；下次使用同一 session 文件继续。")
                return result
            if answer == ":back":
                previous = []
                for candidate in QUESTIONS:
                    if candidate.field == question["field"]:
                        break
                    g, k = candidate.field.split(".")
                    if k in session["configuration"][g]:
                        previous.append(candidate)
                override = previous[-1].public() if previous else None
                if not previous:
                    write("已经是第一题。")
                continue
            if answer == "" and default is not None:
                answer = default
            elif question["kind"] == "choice" and answer.isdecimal():
                index = int(answer) - 1
                if not 0 <= index < len(question["choices"]):
                    raise SetupError("SETUP_CHOICE_UNSUPPORTED")
                answer = question["choices"][index][0]
            elif question["kind"] == "products":
                answer = read_input_file(answer)
            result = store.answer({question["field"]: answer}, session["revision"])
            if result["invalidated_fields"]:
                write("目标发生变化，相关配置需要重新填写：" + ", ".join(result["invalidated_fields"]))
            override = None
        except (EOFError, KeyboardInterrupt):
            write("\n已保存此前完成的步骤；可使用同一 session 文件继续。")
            result = describe(store.read())
            result["interrupted"] = True
            return result
        except SetupError as error:
            write("未保存此项：" + str(error))
        except (OSError, UnicodeError):
            write("未保存此项：SETUP_INPUT_OR_STORAGE_UNAVAILABLE")


def run_setup(args):
    try:
        if args.answers is None and args.expect_revision is not None:
            raise SetupError("SETUP_EXPECT_REVISION_REQUIRES_ANSWERS")
        if args.answers is not None and args.expect_revision is None:
            raise SetupError("SETUP_EXPECT_REVISION_REQUIRED")
        if args.interactive and args.answers is not None:
            raise SetupError("SETUP_INTERACTIVE_AND_ANSWERS_CONFLICT")
        if args.interactive and not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise SetupError("SETUP_TTY_REQUIRED_USE_JSON")
        answers = None
        if args.answers is not None:
            answers = read_json(sys.stdin) if args.answers == "-" else read_input_file(args.answers)
        source = SessionStore(args.from_session).read() if args.from_session else None
        store = SessionStore(args.session)
        session = store.start(source)
        if answers is not None:
            result = store.answer(answers, args.expect_revision)
        elif args.interactive or (not args.json and sys.stdin.isatty() and sys.stdout.isatty()):
            result = interactive(store)
        else:
            result = describe(session)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 130 if result.get("interrupted") else 0
    except SetupError as error:
        print(json.dumps({"error": str(error), "runtime_status": "not_running", "execute_allowed": False}))
        return 2
    except (KeyboardInterrupt, EOFError):
        print(json.dumps({"error": "SETUP_INTERRUPTED", "runtime_status": "not_running", "execute_allowed": False}))
        return 130
    except (OSError, UnicodeError):
        # Never echo a failing filename, malformed JSON or secret-bearing input.
        print(json.dumps({"error": "SETUP_IO_ERROR", "runtime_status": "not_running", "execute_allowed": False}))
        return 2
