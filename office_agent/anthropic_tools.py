from anthropic import beta_tool

from tools import PLAIN_TOOLS

ALL_TOOLS = [beta_tool(fn) for fn in PLAIN_TOOLS]
