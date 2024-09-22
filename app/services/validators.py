import re


url_validator_re = re.compile(r"https?:\/\/(www)?\.roblox\.com\/game-pass\/(\d*)\/")
full_url_validator_re = re.compile(r"https?:\/\/(www)?\.roblox\.com\/game-pass\/(\d*)/(\w*)?")


def validate_game_pass_url(url: str) -> bool:
    match = url_validator_re.search(url)

    if match:
        return True

    match = full_url_validator_re.search(url)
    if match:
        return True

    groups = match.groups()

    if len(groups) != 3:
        return False
    if not groups[2]:
        return False
    return True
