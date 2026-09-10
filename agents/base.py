"""
MULTI-AGENT LAYER - foundations
==============================================================================
The control tower runs a small society of specialised agents on top of the
deterministic planning engine.

Design rule that matters
------------------------
    AGENTS NEVER DO ARITHMETIC.

Every number an agent reports is read from a table the engine computed. Agents
perceive that state, reason about what it means, and propose actions expressed
as engine overrides. This keeps the plan auditable - an examiner can trace any
figure on screen back to an MRP row - while the agents supply the judgement
layer that a plain dashboard cannot.

Each agent implements the classic perceive -> reason -> act loop:

    observe(blackboard)  read the shared planning state
    reason()             turn observations into findings
    act()                turn findings into proposed actions

A Finding is something true about the plan. An Action is something that could
be done about it, expressed as an override the pipeline can execute.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

SEVERITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Info": 4}


@dataclass
class Finding:
    """Something an agent has concluded about the plan."""
    agent: str
    title: str
    detail: str
    severity: str = "Info"           # Critical / High / Medium / Low / Info
    module: str = ""
    item: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def rank(self):
        return SEVERITY_RANK.get(self.severity, 9)


@dataclass
class Action:
    """A proposed intervention, expressed as an executable engine override."""
    agent: str
    label: str
    rationale: str
    overrides: Dict[str, Any] = field(default_factory=dict)
    rerun_from: Optional[str] = None
    expected_effect: str = ""
    confidence: str = "Medium"       # High / Medium / Low


@dataclass
class Message:
    """One entry in the inter-agent conversation log."""
    sender: str
    recipient: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))


class Blackboard:
    """Shared workspace the agents read from and write to.

    A blackboard architecture suits this problem because the agents are not in a
    fixed pipeline: the exception agent wants whatever the inventory, supply and
    capacity agents happened to find, and the coordinator wants everything.
    """

    def __init__(self, result):
        self.result = result                       # the engine's output
        self.findings: List[Finding] = []
        self.actions: List[Action] = []
        self.messages: List[Message] = []

    # -- writing ---------------------------------------------------------
    def post_finding(self, finding: Finding):
        self.findings.append(finding)
        return finding

    def post_action(self, action: Action):
        self.actions.append(action)
        return action

    def send(self, sender, recipient, content):
        self.messages.append(Message(sender, recipient, content))

    # -- reading ---------------------------------------------------------
    def findings_by(self, agent=None, severity=None, module=None):
        out = self.findings
        if agent:
            out = [f for f in out if f.agent == agent]
        if severity:
            out = [f for f in out if f.severity == severity]
        if module:
            out = [f for f in out if f.module == module]
        return sorted(out, key=lambda f: f.rank)

    def top_findings(self, n=5):
        return sorted(self.findings, key=lambda f: f.rank)[:n]

    def table(self, key):
        """Convenience accessor for an engine result table."""
        return self.result.get(key)

    def kpi(self, key, default=None):
        return self.result.get("kpis", {}).get(key, default)


class Agent:
    """Base class for every agent in the control tower."""

    name = "agent"
    role = ""

    def __init__(self, blackboard: Blackboard):
        self.bb = blackboard
        self.observations: Dict[str, Any] = {}

    # -- the perceive / reason / act loop --------------------------------
    def observe(self):
        """Read the state this agent cares about."""
        raise NotImplementedError

    def reason(self) -> List[Finding]:
        """Turn observations into findings."""
        return []

    def act(self) -> List[Action]:
        """Turn findings into proposed actions."""
        return []

    def run(self):
        self.observe()
        findings = self.reason() or []
        for f in findings:
            self.bb.post_finding(f)
        actions = self.act() or []
        for a in actions:
            self.bb.post_action(a)
        return findings, actions

    # -- helpers ----------------------------------------------------------
    def finding(self, title, detail, severity="Info", module="", item="", **evidence):
        return Finding(agent=self.name, title=title, detail=detail,
                       severity=severity, module=module, item=item,
                       evidence=evidence)

    def action(self, label, rationale, overrides=None, rerun_from=None,
               expected_effect="", confidence="Medium"):
        return Action(agent=self.name, label=label, rationale=rationale,
                      overrides=overrides or {}, rerun_from=rerun_from,
                      expected_effect=expected_effect, confidence=confidence)
