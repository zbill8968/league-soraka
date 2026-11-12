"""Normalize the two team-label formats used by Live Client Data and LCU."""


def team_key(team):
    text = str(team or "").strip().upper()
    if text in ("100", "ORDER", "TEAM_100", "TEAM1"):
        return "ORDER"
    if text in ("200", "CHAOS", "TEAM_200", "TEAM2"):
        return "CHAOS"
    return text
