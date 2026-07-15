import re


def lexical_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for part in re.findall(r"[A-Za-z0-9_./:+-]+|[\u4e00-\u9fff]+", value.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) == 1:
                tokens.append(part)
            else:
                tokens.extend(part[index:index + 2] for index in range(len(part) - 1))
        else:
            tokens.append(part)
    return list(dict.fromkeys(tokens))


def lexical_text(value: str) -> str:
    return " ".join(lexical_tokens(value))
