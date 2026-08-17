"""Cycle 253's digest rewrite: drop the retired Needs Edvard heading, retire
the finished handoff item, renumber, and prepend this cycle's line."""
import re
import sys

live = open("live.md").read()

# 1. The `## Needs Edvard` heading and its body go. `parse_digest` stopped
#    reading it in #236, and the heading survived only because it did.
a = live.index("## Needs Edvard")
b = live.index("## Next cycle")
live = live[:a] + live[b:]

# 2. Item 1 is done (#236). Drop it and renumber what follows.
start = live.index("1. **[delete-dead-needs-code]**")
end = live.index("2. **[fix-composite-circuit-breaker]**")
live = live[:start] + live[end:]
live = re.sub(r"^(\d+)\. \*\*\[", lambda m: f"{int(m.group(1)) - 1}. **[", live, flags=re.M)

# 3. The new item this cycle found, appended to Live work.
new_item = """
12. **[surface-oldest-unanswered-ask]** An ask on a journal card has no "still waiting" signal. #94's has been unanswered on card 247 since 08-16 21:20 and is now six cards down the feed, while the row it blocks is the top of Edvard's board. Cycle 247 replaced visibility-forever with visibility-at-all. Surface the oldest unanswered ask — on the status header, or beside the rows in `top_board_rows`. "Unanswered" is `nova_comments` having no comment on that cycle's card, which #232 already computes.
"""
marker = "\n- **[deadline-contract-drift]**"
live = live.replace(marker, new_item + marker, 1)

# 4. This cycle's digest line, newest first.
line = open("digest-line.md").read().strip()
head = "## Digest\n\n"
i = live.index(head) + len(head)
live = live[:i] + line + "\n\n" + live[i:]

open("live.md", "w").write(live)
print("needs heading removed, item 1 retired, item 12 added, digest line prepended")
