"""Optionally load development sample recipes into an empty database.

Користење:
    python seed.py --include-samples
"""
import argparse
import json
from pathlib import Path

from database import SessionLocal, init_db
from models import (
    Equipment,
    Ingredient,
    Recipe,
    RecipeIngredient,
    normalize_name,
)

RECIPES_FILE = Path(__file__).resolve().parent / "recipes.json"

# Основни состојки што се претпоставува дека секој ги има дома.
STAPLES = {"сол", "црн пипер", "масло", "вода"}


def get_ingredient(db_session, name):
    name = normalize_name(name)
    ingredient = db_session.query(Ingredient).filter_by(name=name).first()
    if ingredient is None:
        ingredient = Ingredient(name=name, is_staple=name in STAPLES)
        db_session.add(ingredient)
    return ingredient


def get_equipment(db_session, name):
    name = normalize_name(name)
    equipment = db_session.query(Equipment).filter_by(name=name).first()
    if equipment is None:
        equipment = Equipment(name=name)
        db_session.add(equipment)
    return equipment


def seed(db_session):
    with open(RECIPES_FILE, encoding="utf-8") as f:
        data = json.load(f)

    for item in data:
        recipe = Recipe(
            title=item["title"],
            description=item["description"],
            meal_type=item["meal_type"],
            time_minutes=item["time_minutes"],
            steps="\n".join(item["steps"]),
        )
        db_session.add(recipe)
        recipe.equipment = [get_equipment(db_session, name) for name in item["equipment"]]

        for ing in item["ingredients"]:
            link = RecipeIngredient(quantity=ing["quantity"])
            link.ingredient = get_ingredient(db_session, ing["name"])
            recipe.ingredient_links.append(link)

    db_session.commit()
    print(f"Added {len(data)} recipes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-samples",
        action="store_true",
        help="load bundled sample recipes into an otherwise empty database",
    )
    args = parser.parse_args()

    init_db()

    session = SessionLocal()
    try:
        if not args.include_samples:
            print("Sample recipes are disabled. Use --include-samples to add them.")
        elif session.query(Recipe).count() > 0:
            print("The database already contains recipes; sample recipes were not added.")
        else:
            seed(session)
    finally:
        session.close()
