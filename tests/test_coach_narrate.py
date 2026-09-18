import json
from types import SimpleNamespace

from poker_table.agents import CallingStation, Maniac, TightAggressive
from poker_table.coach.facts import tag_hands
from poker_table.coach.narrate import NOTES_SCHEMA, narrate, report_for_model
from poker_table.coach.report import build_report
from poker_table.league import LeagueConfig, run_league


class FakeClient:
    def __init__(self, reply, stop="end_turn"):
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)
        self.reply = reply
        self.stop = stop

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason=self.stop,
            usage=SimpleNamespace(
                input_tokens=2000,
                output_tokens=400,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


def maniac_report():
    agents = [TightAggressive("tag"), Maniac("maniac", seed=1), CallingStation("station")]
    histories = run_league(agents, LeagueConfig(hands=40, seed=3)).histories
    return build_report(histories, "maniac", tag_hands(histories, "maniac", samples=40))


def test_model_payload_is_a_slice_of_the_report() -> None:
    report = maniac_report()
    payload = report_for_model(report, top=2, examples=3)
    assert payload["player"] == "maniac" and len(payload["leaks"]) <= 2
    assert all(len(leak["examples"]) <= 3 for leak in payload["leaks"])
    assert "facts" not in payload
    assert json.dumps(payload)


def test_narration_keeps_supported_notes_and_drops_the_rest() -> None:
    report = maniac_report()
    first = report.leaks[0]
    good_hand = first.examples[0].hand_id
    reply = {
        "notes": [
            {
                "tag": first.tag,
                "note": "You open far too wide.",
                "cited_hands": [f"#{good_hand}"],
                "one_thing": "Use the chart.",
            },
            {
                "tag": first.tag,
                "note": "Invented hand.",
                "cited_hands": ["999999"],
                "one_thing": "x",
            },
            {"tag": "made_up_leak", "note": "Nope.", "cited_hands": [good_hand], "one_thing": "x"},
            {"tag": first.tag, "note": "No citations.", "cited_hands": [], "one_thing": "x"},
        ],
        "focus": "Tighten up preflop.",
    }
    client = FakeClient(reply)
    narration = narrate(report, client=client, top=3)
    assert len(narration.notes) == 1 and narration.dropped == 3
    note = narration.notes[0]
    assert note.title == first.title and note.cited_hands == (good_hand,)
    assert narration.focus == "Tighten up preflop."
    assert narration.cost_usd > 0
    text = narration.render()
    assert f"#{good_hand}" in text and "Do this: Use the chart." in text
    request = client.requests[0]
    assert request["output_config"]["format"]["schema"] == NOTES_SCHEMA
    assert request["model"] == "claude-opus-5"
    assert "only cite hands" in request["system"][0]["text"]
    body = json.loads(request["messages"][0]["content"])
    assert body["player"] == "maniac"


def test_narration_handles_bad_replies_and_empty_reports() -> None:
    report = maniac_report()
    assert narrate(report, client=FakeClient("not json")).notes == ()
    assert narrate(report, client=FakeClient({"notes": []}, stop="max_tokens")).notes == ()
    empty = build_report([], "ghost", [])
    assert narrate(empty, client=FakeClient({"notes": []})).render() == ""
