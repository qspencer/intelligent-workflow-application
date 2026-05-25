from workflow_platform.triggers.base import Trigger, TriggerCallback
from workflow_platform.triggers.filesystem import FilesystemTrigger
from workflow_platform.triggers.gmail_poll import GmailPollTrigger
from workflow_platform.triggers.schedule import ScheduleTrigger
from workflow_platform.triggers.webhook import WebhookRegistry, WebhookTrigger

__all__ = [
    "FilesystemTrigger",
    "GmailPollTrigger",
    "ScheduleTrigger",
    "Trigger",
    "TriggerCallback",
    "WebhookRegistry",
    "WebhookTrigger",
]
