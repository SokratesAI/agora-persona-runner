"""Agora persona runner, as a real package instead of an embedded ConfigMap
script (migrated 2026-07-29, Edvard's explicit ask). This __init__ re-exports
every public name from every submodule into one flat namespace -- purely so
the existing test suite (written against a single flattened `runner` module)
keeps working unchanged; new code should still import from the specific
submodule that actually owns a name (agora_runner.vault, agora_runner.tools_github,
etc.), not from this facade.
"""
from agora_runner.config import *  # noqa: F401,F403
from agora_runner.log import *  # noqa: F401,F403
from agora_runner.http_util import *  # noqa: F401,F403
from agora_runner.vault import *  # noqa: F401,F403
from agora_runner.turns import *  # noqa: F401,F403
from agora_runner.agora_api import *  # noqa: F401,F403
from agora_runner.tools_kubectl import *  # noqa: F401,F403
from agora_runner.tools_github import *  # noqa: F401,F403
from agora_runner.tools_github import _github_api  # noqa: F401 -- "from X import *" skips leading-underscore names
from agora_runner.tools_terminal import *  # noqa: F401,F403
from agora_runner.tools_search import *  # noqa: F401,F403
from agora_runner.tools_schemas import *  # noqa: F401,F403
from agora_runner.audit import *  # noqa: F401,F403
from agora_runner.tools_dispatch import *  # noqa: F401,F403
from agora_runner.providers.anthropic import *  # noqa: F401,F403
from agora_runner.providers.anthropic import _anthropic_content  # noqa: F401 -- underscore name, see above
from agora_runner.providers.gemini import *  # noqa: F401,F403
from agora_runner.providers.gemini import _gemini_parts  # noqa: F401 -- underscore name, see above
from agora_runner.providers.claude_cli import *  # noqa: F401,F403
from agora_runner.reply import *  # noqa: F401,F403
from agora_runner.workflows import *  # noqa: F401,F403
from agora_runner.conversations import *  # noqa: F401,F403
from agora_runner.conversations import _conversation_failures  # noqa: F401 -- underscore name, see above
from agora_runner.heartbeats import *  # noqa: F401,F403
from agora_runner.poll import *  # noqa: F401,F403
from agora_runner.invoke_server import *  # noqa: F401,F403
from agora_runner.main import *  # noqa: F401,F403

import subprocess  # noqa: F401 -- tests patch runner.subprocess directly
