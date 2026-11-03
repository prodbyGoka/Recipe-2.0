def score_recipe(recipe, user_ingredients):
    needed = {i.strip().lower() for i in recipe.main_ingredient_names}
    have = {i.strip().lower() for i in user_ingredients if i.strip()}
    matched = needed & have
    missing = needed - have
    score = round(len(matched) / len(needed) * 100) if needed else 0
    return score, sorted(matched), sorted(missing)
