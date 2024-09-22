import re


url_validator_re = re.compile(r"https?:\/\/(www)?\.roblox\.com\/game-pass\/(\d*)\/")


def validate_game_pass_url(url: str) -> bool:
    match = url_validator_re.search(url)

    if not match:
        return False

    groups = match.groups()

    if len(groups) != 3:
        return False
    if not groups[2]:
        return False
    return True
