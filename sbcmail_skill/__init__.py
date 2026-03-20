"""SBCMail Skill — give your AI agent async messaging superpowers.

Quick start:
    from sbcmail_skill import SBCMailbox

    mailbox = SBCMailbox("my-agent")

    @mailbox.on("chat")
    def handle(msg):
        mailbox.reply(msg, intent="chat", payload={"body": "Got it!"})

    mailbox.run()
"""

from .mailbox import SBCMailbox
from .models import PollMessage, SendResult, MessageDetail

__version__ = "0.1.0"
__all__ = ["SBCMailbox", "PollMessage", "SendResult", "MessageDetail"]
