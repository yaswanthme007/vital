from pydantic import BaseModel, ConfigDict


def to_camel(snake: str) -> str:
    first, *rest = snake.split("_")
    return first + "".join(word.capitalize() for word in rest)


class CamelModel(BaseModel):
    """Base model that emits camelCase JSON (matching the TS frontend)
    while still accepting either camelCase or snake_case on input."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )
