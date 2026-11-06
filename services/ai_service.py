import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AIConfigurationError(Exception):
    pass


class AIServiceError(Exception):
    pass


class AIResponseError(Exception):
    pass


RECIPE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "recipes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "description": {"type": "STRING"},
                    "meal_type": {
                        "type": "STRING",
                        "enum": ["појадок", "ручек", "вечера"],
                    },
                    "time_minutes": {"type": "INTEGER"},
                    "ingredients": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "name": {"type": "STRING"},
                                "quantity": {"type": "STRING"},
                            },
                            "required": ["name", "quantity"],
                        },
                    },
                    "steps": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                    "explanation": {"type": "STRING"},
                    "substitutions": {"type": "STRING"},
                    "tips": {"type": "STRING"},
                },
                "required": [
                    "title",
                    "description",
                    "meal_type",
                    "time_minutes",
                    "ingredients",
                    "steps",
                    "explanation",
                    "substitutions",
                    "tips",
                ],
            },
        }
    },
    "required": ["recipes"],
}


def _validate_recipe(recipe, max_time, requested_meal_type):
    if not isinstance(recipe, dict):
        raise AIResponseError("The AI returned a recipe in an unexpected format.")

    text_fields = (
        "title",
        "description",
        "meal_type",
        "explanation",
        "substitutions",
        "tips",
    )
    if any(not isinstance(recipe.get(field), str) for field in text_fields):
        raise AIResponseError("The AI returned a recipe with missing text fields.")

    title = recipe["title"].strip()
    if not title or len(title) > 150:
        raise AIResponseError("The AI returned a recipe with an invalid title.")
    if recipe["meal_type"] not in {"појадок", "ручек", "вечера"}:
        raise AIResponseError("The AI returned an invalid meal type.")
    if requested_meal_type and recipe["meal_type"] != requested_meal_type:
        raise AIResponseError("The AI returned a recipe for a different meal type.")

    time_minutes = recipe.get("time_minutes")
    if (
            isinstance(time_minutes, bool)
            or not isinstance(time_minutes, int)
            or not 1 <= time_minutes <= max_time
    ):
        raise AIResponseError("The AI returned a recipe outside the requested time limit.")

    ingredients = recipe.get("ingredients")
    if not isinstance(ingredients, list) or not 1 <= len(ingredients) <= 30:
        raise AIResponseError("The AI returned a recipe with an invalid ingredient list.")
    for ingredient in ingredients:
        if (
                not isinstance(ingredient, dict)
                or not isinstance(ingredient.get("name"), str)
                or not ingredient["name"].strip()
                or len(ingredient["name"]) > 100
                or not isinstance(ingredient.get("quantity"), str)
                or len(ingredient["quantity"]) > 50
        ):
            raise AIResponseError("The AI returned an invalid recipe ingredient.")

    steps = recipe.get("steps")
    if (
            not isinstance(steps, list)
            or not 1 <= len(steps) <= 20
            or any(not isinstance(step, str) or not step.strip() for step in steps)
    ):
        raise AIResponseError("The AI returned a recipe with invalid preparation steps.")

    return {
        **recipe,
        "title": title,
        "description": recipe["description"].strip(),
        "ingredients": [
            {**ingredient, "name": ingredient["name"].strip()}
            for ingredient in ingredients
        ],
        "steps": [step.strip() for step in steps],
    }


def recommend_recipes(
    ingredients, max_time=60, meal_type=None, diet="No preference",
    allergens="", has_basics=True, language="en",
):
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise AIConfigurationError(
            "Set GEMINI_API_KEY in the server environment to enable AI recipe suggestions."
        )

    model = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
    output_language = "Macedonian" if language == "mk" else "English"
    prompt = (
        "You are a practical home cooking assistant. Suggest exactly three distinct recipes. "
        f"Write recipe titles, descriptions, ingredient names, steps, explanations, substitutions, and tips in {output_language}. "
        "The meal_type field must use exactly one of these Macedonian values: појадок, ручек, вечера. "
        "Use the user's ingredients as the main ingredients. Do not claim that ingredients are available unless listed. "
        f"The user {'has' if has_basics else 'does not have'} pantry basics such as salt, pepper, and oil. "
        f"Diet preference: {diet}. Strictly avoid these allergens: {allergens or 'none specified'}. "
        f"Every recipe must take no more than {max_time} minutes. Meal type: {meal_type or 'any'}. "
        f"Available ingredients: {json.dumps(ingredients, ensure_ascii=False)}. "
        "Include realistic quantities, safe and clear cooking steps, and concise explanations, substitutions, and serving tips."
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RECIPE_SCHEMA,
            "temperature": 0.7,
            "maxOutputTokens": 6000,
        },
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )

    try:
        with urlopen(request, timeout=45) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            error_body = json.loads(error.read().decode("utf-8"))
            error_info = error_body.get("error", {})
            error_message = (
                error_info.get("message", "") if isinstance(error_info, dict) else ""
            )
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            error_message = ""
        if not isinstance(error_message, str):
            error_message = ""
        if api_key:
            error_message = error_message.replace(api_key, "[redacted]")
        if error.code == 429:
            raise AIServiceError(
                "Gemini request limit reached. Wait and try again."
                + (f" Details: {error_message}" if error_message else "")
            ) from error
        raise AIServiceError(
            f"Gemini API error (HTTP {error.code}). "
            + (
                error_message
                if error_message
                else "Check your API key, model, and Google AI Studio project."
            )
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise AIServiceError(
            "Could not reach the AI service. Check the server's internet connection and try again."
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise AIResponseError("The AI service returned an unreadable response.") from error

    try:
        candidate = response_data["candidates"][0]
        content = candidate["content"]["parts"]
        response_text = "".join(part["text"] for part in content if "text" in part)
        result = json.loads(response_text)
        recipes = result["recipes"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        if isinstance(response_data, dict):
            feedback = response_data.get("promptFeedback", {})
            block_reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
            if block_reason:
                raise AIResponseError(
                    f"Gemini blocked the recipe prompt ({block_reason})."
                ) from error
        raise AIResponseError("The AI service did not return usable recipe suggestions.") from error

    if not isinstance(recipes, list) or not 1 <= len(recipes) <= 3:
        raise AIResponseError("The AI service returned an unexpected number of recipes.")
    return [
        _validate_recipe(recipe, max_time, meal_type)
        for recipe in recipes
    ]
