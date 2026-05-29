#!/usr/bin/env python3
"""A small email-sending agent with safe drafting, validation, and SMTP delivery.

The agent is intentionally dependency-free: it can draft practical emails from a
simple goal, render templates, preview messages in dry-run mode, and send through
any SMTP server configured with environment variables or CLI flags.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path
from string import Template
from typing import Mapping, Sequence

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DEFAULT_SMTP_PORT = 587
DEFAULT_TONE = "professional"
TONE_OPENERS = {
    "professional": "您好，",
    "friendly": "你好，",
    "concise": "您好，",
    "formal": "尊敬的收件人：",
}
TONE_CLOSERS = {
    "professional": "谢谢，",
    "friendly": "谢谢你，",
    "concise": "谢谢，",
    "formal": "此致，",
}


@dataclass(slots=True)
class EmailDraft:
    """An email draft ready to preview or send."""

    subject: str
    body: str


@dataclass(slots=True)
class EmailTask:
    """Structured instructions for the email agent."""

    to: list[str]
    sender: str
    subject: str | None = None
    body: str | None = None
    goal: str | None = None
    tone: str = DEFAULT_TONE
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    sender_name: str | None = None
    signature: str | None = None
    template: str | None = None
    variables: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class SmtpConfig:
    """SMTP connection settings."""

    host: str
    port: int = DEFAULT_SMTP_PORT
    username: str | None = None
    password: str | None = None
    use_ssl: bool = False
    starttls: bool = True
    timeout: int = 30


class EmailAgent:
    """Draft, validate, preview, and send emails."""

    def draft(self, task: EmailTask) -> EmailDraft:
        """Create an email draft from explicit body, template, or goal."""

        validate_recipients(task.to, "to")
        validate_recipients(task.cc, "cc")
        validate_recipients(task.bcc, "bcc")
        validate_email_address(task.sender, "sender")

        subject = task.subject or infer_subject(task.goal, task.body, task.template)
        if task.template:
            body = render_template(task.template, task.variables)
        elif task.body:
            body = task.body.strip()
        elif task.goal:
            body = compose_from_goal(task.goal, task.tone)
        else:
            raise ValueError("请提供 --body、--template-file 或 --goal 之一。")

        if task.signature:
            body = append_signature(body, task.signature)
        return EmailDraft(subject=subject.strip(), body=body.strip())

    def build_message(self, task: EmailTask, draft: EmailDraft) -> EmailMessage:
        """Convert a draft into an EmailMessage."""

        message = EmailMessage()
        sender = formataddr((task.sender_name or "", task.sender))
        message["From"] = sender
        message["To"] = ", ".join(task.to)
        if task.cc:
            message["Cc"] = ", ".join(task.cc)
        message["Subject"] = draft.subject
        message.set_content(draft.body)
        return message

    def preview(self, task: EmailTask, draft: EmailDraft) -> str:
        """Return a human-readable preview without exposing Bcc in headers."""

        lines = [
            "=== 邮件预览（未发送）===",
            f"From: {formataddr((task.sender_name or '', task.sender))}",
            f"To: {', '.join(task.to)}",
        ]
        if task.cc:
            lines.append(f"Cc: {', '.join(task.cc)}")
        if task.bcc:
            lines.append(f"Bcc: {len(task.bcc)} 位收件人（发送时隐藏）")
        lines.extend([f"Subject: {draft.subject}", "", draft.body])
        return "\n".join(lines)

    def send(self, task: EmailTask, draft: EmailDraft, config: SmtpConfig) -> None:
        """Send a drafted email through SMTP."""

        message = self.build_message(task, draft)
        recipients = [*task.to, *task.cc, *task.bcc]
        if config.use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                config.host, config.port, timeout=config.timeout, context=context
            ) as smtp:
                login_if_needed(smtp, config)
                smtp.send_message(message, to_addrs=recipients)
            return

        with smtplib.SMTP(config.host, config.port, timeout=config.timeout) as smtp:
            if config.starttls:
                smtp.starttls(context=ssl.create_default_context())
            login_if_needed(smtp, config)
            smtp.send_message(message, to_addrs=recipients)


def validate_email_address(value: str, label: str) -> None:
    """Validate a single email address."""

    _, address = parseaddr(value)
    if not address or not EMAIL_RE.match(address):
        raise ValueError(f"{label} 邮箱地址无效: {value}")


def validate_recipients(values: Sequence[str], label: str) -> None:
    """Validate a recipient list."""

    if label == "to" and not values:
        raise ValueError("请至少提供一个 --to 收件人。")
    for value in values:
        validate_email_address(value, label)


def infer_subject(goal: str | None, body: str | None, template: str | None) -> str:
    """Infer a concise subject when the user did not provide one."""

    source = goal or body or template or "邮件"
    first_line = next((line.strip() for line in source.splitlines() if line.strip()), "邮件")
    first_line = re.sub(r"\s+", " ", first_line)
    return first_line[:50] or "邮件"


def render_template(template_text: str, variables: Mapping[str, str]) -> str:
    """Render a string.Template using provided variables."""

    return Template(template_text).safe_substitute(variables).strip()


def compose_from_goal(goal: str, tone: str) -> str:
    """Draft a practical Chinese email from a goal/purpose."""

    normalized_tone = tone if tone in TONE_OPENERS else DEFAULT_TONE
    opener = TONE_OPENERS[normalized_tone]
    closer = TONE_CLOSERS[normalized_tone]
    goal_text = goal.strip().rstrip("。.!！")
    if normalized_tone == "concise":
        return f"{opener}\n\n{goal_text}。\n\n{closer}"
    return (
        f"{opener}\n\n"
        f"我想和您沟通以下事项：{goal_text}。\n\n"
        "如果方便的话，烦请您回复确认，或告知下一步需要我补充的信息。\n\n"
        f"{closer}"
    )


def append_signature(body: str, signature: str) -> str:
    """Append a signature block to an email body."""

    return f"{body.rstrip()}\n\n{signature.strip()}"


def parse_variables(raw_values: Sequence[str], json_file: str | None) -> dict[str, str]:
    """Parse template variables from KEY=VALUE pairs and an optional JSON file."""

    variables: dict[str, str] = {}
    if json_file:
        data = json.loads(Path(json_file).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("--variables-file 必须是 JSON 对象。")
        variables.update({str(key): str(value) for key, value in data.items()})
    for raw in raw_values:
        if "=" not in raw:
            raise ValueError(f"变量格式应为 KEY=VALUE: {raw}")
        key, value = raw.split("=", 1)
        variables[key] = value
    return variables


def config_from_args(args: argparse.Namespace) -> SmtpConfig:
    """Build SMTP config from CLI flags and environment variables."""

    host = args.smtp_host or os.getenv("SMTP_HOST")
    if not host:
        raise ValueError("发送邮件需要 --smtp-host 或 SMTP_HOST 环境变量。")
    return SmtpConfig(
        host=host,
        port=args.smtp_port,
        username=args.smtp_username or os.getenv("SMTP_USERNAME"),
        password=args.smtp_password or os.getenv("SMTP_PASSWORD"),
        use_ssl=args.smtp_ssl,
        starttls=not args.no_starttls,
        timeout=args.timeout,
    )


def login_if_needed(smtp: smtplib.SMTP, config: SmtpConfig) -> None:
    """Log in when SMTP credentials are configured."""

    if config.username or config.password:
        if not config.username or not config.password:
            raise ValueError("SMTP 用户名和密码需要同时提供。")
        smtp.login(config.username, config.password)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="能帮你起草、预览并通过 SMTP 发送邮件的轻量机器人智能体。"
    )
    parser.add_argument("--to", action="append", default=[], help="收件人，可重复传入。")
    parser.add_argument("--cc", action="append", default=[], help="抄送人，可重复传入。")
    parser.add_argument("--bcc", action="append", default=[], help="密送人，可重复传入。")
    parser.add_argument("--from", dest="sender", required=True, help="发件人邮箱。")
    parser.add_argument("--from-name", help="发件人显示名。")
    parser.add_argument("--subject", help="邮件主题；不传则自动从内容推断。")
    parser.add_argument("--body", help="完整邮件正文。")
    parser.add_argument("--goal", help="告诉智能体邮件目的，由它生成正文。")
    parser.add_argument(
        "--tone",
        choices=sorted(TONE_OPENERS.keys()),
        default=DEFAULT_TONE,
        help="起草语气。",
    )
    parser.add_argument("--signature", help="签名，会追加到正文末尾。")
    parser.add_argument("--template-file", help="邮件模板文件，支持 $name 变量。")
    parser.add_argument(
        "--var",
        action="append",
        default=[],
        help="模板变量 KEY=VALUE，可重复传入。",
    )
    parser.add_argument("--variables-file", help="模板变量 JSON 文件。")
    parser.add_argument("--smtp-host", help="SMTP 主机；也可用 SMTP_HOST。")
    parser.add_argument("--smtp-port", type=int, default=DEFAULT_SMTP_PORT)
    parser.add_argument("--smtp-username", help="SMTP 用户名；也可用 SMTP_USERNAME。")
    parser.add_argument("--smtp-password", help="SMTP 密码；也可用 SMTP_PASSWORD。")
    parser.add_argument("--smtp-ssl", action="store_true", help="使用 SMTP_SSL。")
    parser.add_argument(
        "--no-starttls", action="store_true", help="普通 SMTP 连接不启用 STARTTLS。"
    )
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--send",
        action="store_true",
        help="真正发送邮件；默认只预览，避免误发。",
    )
    return parser.parse_args(argv)


def task_from_args(args: argparse.Namespace) -> EmailTask:
    """Build an EmailTask from parsed arguments."""

    template = None
    if args.template_file:
        template = Path(args.template_file).read_text(encoding="utf-8")
    return EmailTask(
        to=args.to,
        cc=args.cc,
        bcc=args.bcc,
        sender=args.sender,
        sender_name=args.from_name,
        subject=args.subject,
        body=args.body,
        goal=args.goal,
        tone=args.tone,
        signature=args.signature,
        template=template,
        variables=parse_variables(args.var, args.variables_file),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""

    args = parse_args(argv or sys.argv[1:])
    agent = EmailAgent()
    task = task_from_args(args)
    draft = agent.draft(task)
    print(agent.preview(task, draft))
    if not args.send:
        print("\n默认是安全预览模式；确认无误后添加 --send 才会发送。")
        return 0
    agent.send(task, draft, config_from_args(args))
    print("\n邮件已发送。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
