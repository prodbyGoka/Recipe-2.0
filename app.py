import hmac
import os
import re
import secrets
from functools import wraps
from urllib.parse import urlsplit

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from database import SessionLocal, init_db
from models import AIResult, Ingredient, Recipe, RecipeIngredient, RecipeReview, SavedRecipe, Search, User, normalize_name
from services.ai_service import (
    AIConfigurationError,
    AIResponseError,
    AIServiceError,
    recommend_recipes,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
init_db()


def add_sample_recipes_for_new_database():
    db_session = SessionLocal()
    try:
        if db_session.query(Recipe.id).first() is None:
            from seed import seed

            seed(db_session)
    finally:
        db_session.close()


add_sample_recipes_for_new_database()

RECIPE_IMAGES = [
    "figma-food-01.png", "figma-food-02.jpg", "figma-food-03.jpg", "figma-food-04.jpg",
    "figma-food-05.png", "figma-food-06.png", "figma-food-07.jpg", "figma-food-08.png",
]


def recipe_image(recipe_id):
    return url_for("static", filename=f"images/recipes/{RECIPE_IMAGES[(recipe_id - 1) % len(RECIPE_IMAGES)]}")


def get_db():
    if "db_session" not in g:
        g.db_session = SessionLocal()
    return g.db_session


@app.teardown_appcontext
def close_db(exception=None):
    db_session = g.pop("db_session", None)
    if db_session is not None:
        if exception is not None:
            db_session.rollback()
        db_session.close()


def signed_in_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if current_user() is None:
            flash("Најавете се за да продолжите.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped_view


def current_user():
    user_id = session.get("user_id")
    if user_id is None:
        return None
    user = get_db().get(User, user_id)
    if user is None:
        session.pop("user_id", None)
    return user


@app.before_request
def validate_csrf_token():
    if request.method == "POST":
        payload = request.get_json(silent=True) if request.is_json else None
        submitted = (
                request.headers.get("X-CSRF-Token")
                or request.form.get("csrf_token", "")
                or (payload.get("csrf_token", "") if isinstance(payload, dict) else "")
        )
        expected = session.get("csrf_token", "")
        if (
                not isinstance(submitted, str)
                or not expected
                or not hmac.compare_digest(submitted, expected)
        ):
            abort(400, description="Неважечки безбедносен токен. Обидете се повторно.")


@app.context_processor
def inject_template_values():
    session_csrf_token()
    return {"current_user": current_user(), "csrf_token": session["csrf_token"], "recipe_image": recipe_image}


def session_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


@app.route("/")
def index():
    recipes = get_db().query(Recipe).order_by(Recipe.id.desc()).limit(6).all()
    return render_template("index.html", recipes=recipes, total_recipes=get_db().query(Recipe).count())


@app.route("/recipes")
def recipe_list():
    db_session = get_db()
    query = db_session.query(Recipe)
    search_text = request.args.get("q", "").strip()
    meal_type = request.args.get("meal_type", "").strip()
    if search_text:
        pattern = f"%{search_text}%"
        query = query.filter(
            or_(
                Recipe.title.ilike(pattern),
                Recipe.description.ilike(pattern),
                Recipe.steps.ilike(pattern),
                Recipe.ingredient_links.any(
                    RecipeIngredient.ingredient.has(Ingredient.name.ilike(pattern))
                ),
            )
        )
    if meal_type:
        query = query.filter(Recipe.meal_type == meal_type)
    recipes = query.order_by(Recipe.title).all()
    return render_template(
        "list.html",
        recipes=recipes,
        search_text=search_text,
        meal_type=meal_type,
    )


@app.route("/create", methods=["GET", "POST"])
def create():
    if request.method == "GET":
        return render_template("create.html")

    try:
        recipes = create_recommendations(request.form)
    except ValueError as error:
        flash(str(error), "error")
        return render_template("create.html"), 400
    except AIConfigurationError as error:
        flash(str(error), "error")
        return render_template("create.html"), 503
    except (AIServiceError, AIResponseError) as error:
        flash(str(error), "error")
        return render_template("create.html"), 502

    return render_template("create.html", recipes=recipes)


@app.route("/saved")
@signed_in_required
def saved_recipes():
    saved = (
        get_db().query(SavedRecipe)
        .filter_by(user_id=session["user_id"])
        .order_by(SavedRecipe.created_at.desc())
        .all()
    )
    return render_template("saved.html", saved=saved)


@app.post("/recipe/<int:recipe_id>/save")
@signed_in_required
def toggle_saved_recipe(recipe_id):
    db_session = get_db()
    recipe = db_session.get(Recipe, recipe_id)
    if recipe is None:
        abort(404)
    saved = db_session.query(SavedRecipe).filter_by(
        user_id=session["user_id"], recipe_id=recipe_id
    ).first()
    if saved:
        db_session.delete(saved)
        flash("Removed from your recipe box.", "success")
    else:
        db_session.add(SavedRecipe(user_id=session["user_id"], recipe_id=recipe_id))
        flash("Saved for another day.", "success")
    db_session.commit()
    destination = request.form.get("next", "")
    parsed_destination = urlsplit(destination)
    if (
        parsed_destination.scheme or parsed_destination.netloc
        or not parsed_destination.path.startswith("/")
        or parsed_destination.path.startswith("//")
        or "\\" in parsed_destination.path
    ):
        destination = url_for("recipe_detail", recipe_id=recipe_id)
    return redirect(destination)


@app.get("/api/csrf-token")
def api_csrf_token():
    return jsonify({"csrf_token": session_csrf_token()})


@app.post("/api/recommendations")
def api_recommendations():
    if not request.is_json:
        return jsonify({"error": "Send the request body as JSON."}), 415
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "The request body must be a JSON object."}), 400

    try:
        recipes = create_recommendations(payload)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except AIConfigurationError as error:
        return jsonify({"error": str(error)}), 503
    except (AIServiceError, AIResponseError) as error:
        return jsonify({"error": str(error)}), 502

    return jsonify({"recipes": recipes}), 201


def create_recommendations(data):
    ingredient_input = data.get("ingredients", [])
    if isinstance(ingredient_input, str):
        ingredient_input = ingredient_input.split(",")
    if not isinstance(ingredient_input, list):
        raise ValueError("Ingredients must be a list of names or comma-separated text.")

    ingredients = []
    seen_ingredients = set()
    for item in ingredient_input:
        if not isinstance(item, str):
            raise ValueError("Each ingredient must be text.")
        name = normalize_name(item)
        if not name:
            continue
        if len(name) > 100:
            raise ValueError("Ingredient names must be 100 characters or fewer.")
        if name not in seen_ingredients:
            ingredients.append(name)
            seen_ingredients.add(name)
    if not ingredients:
        raise ValueError("Add at least one ingredient.")
    if len(ingredients) > 30:
        raise ValueError("Enter no more than 30 ingredients.")

    raw_max_time = data.get("max_time", 60)
    if isinstance(raw_max_time, bool) or not isinstance(raw_max_time, (int, str)):
        raise ValueError("Maximum cooking time must be between 1 and 240 minutes.")
    try:
        max_time = int(raw_max_time)
    except (TypeError, ValueError):
        raise ValueError("Maximum cooking time must be between 1 and 240 minutes.") from None
    if not 1 <= max_time <= 240:
        raise ValueError("Maximum cooking time must be between 1 and 240 minutes.")

    meal_type = data.get("meal_type", "")
    if not isinstance(meal_type, str):
        raise ValueError("Choose a valid meal type.")
    meal_type = meal_type.strip()
    if meal_type and meal_type not in {"појадок", "ручек", "вечера"}:
        raise ValueError("Choose a valid meal type.")

    diet = data.get("diet", "No preference")
    allergens = data.get("allergens", "")
    language = data.get("language", "en")
    has_basics = data.get("has_basics", True)
    if not isinstance(diet, str) or len(diet) > 40:
        raise ValueError("Choose a valid diet preference.")
    if not isinstance(allergens, str) or len(allergens) > 200:
        raise ValueError("Allergen notes must be 200 characters or fewer.")
    if not isinstance(language, str) or language not in {"en", "mk"}:
        raise ValueError("Choose English or Macedonian.")
    if isinstance(has_basics, str):
        has_basics = has_basics.lower() in {"1", "true", "on", "yes"}
    if not isinstance(has_basics, bool):
        raise ValueError("Choose whether you have pantry basics.")

    generated_recipes = recommend_recipes(
        ingredients, max_time, meal_type or None,
        diet=diet, allergens=allergens, has_basics=has_basics, language=language,
    )
    db_session = get_db()
    search = Search(
        ingredients=", ".join(ingredients),
        max_time=max_time,
        equipment="",
        meal_type=meal_type if meal_type else "",
        diet=diet,
        allergens=allergens,
        language=language,
        has_basics=has_basics,
    )
    db_session.add(search)
    db_session.flush()

    saved_recipes = []
    staple_names = {"сол", "црн пипер", "масло", "вода"}
    for recipe_number, generated in enumerate(generated_recipes, start=1):
        title = generated["title"]
        if db_session.query(Recipe.id).filter_by(title=title).first() is not None:
            title = f"{title[:125].rstrip()} (AI {search.id}-{recipe_number})"

        recipe = Recipe(
            title=title,
            description=generated["description"],
            meal_type=generated["meal_type"],
            time_minutes=generated["time_minutes"],
            steps="\n".join(generated["steps"]),
        )
        db_session.add(recipe)
        db_session.flush()

        seen_recipe_ingredients = set()
        for item in generated["ingredients"]:
            name = normalize_name(item["name"])
            if name in seen_recipe_ingredients:
                continue
            seen_recipe_ingredients.add(name)
            ingredient = (
                db_session.query(Ingredient).filter_by(name=name).first()
            )
            if ingredient is None:
                ingredient = Ingredient(
                    name=name,
                    is_staple=name in staple_names,
                )
                db_session.add(ingredient)
                db_session.flush()
            db_session.add(
                RecipeIngredient(
                    recipe_id=recipe.id,
                    ingredient_id=ingredient.id,
                    quantity=item["quantity"],
                )
            )

        db_session.add(
            AIResult(
                recipe_id=recipe.id,
                search_id=search.id,
                explanation=generated["explanation"],
                substitutions=generated["substitutions"],
                tips=generated["tips"],
            )
        )
        saved_recipes.append(
            {
                "id": recipe.id,
                "title": recipe.title,
                "description": recipe.description,
                "meal_type": recipe.meal_type,
                "time_minutes": recipe.time_minutes,
                "ingredients": generated["ingredients"],
                "steps": generated["steps"],
                "explanation": generated["explanation"],
                "substitutions": generated["substitutions"],
                "tips": generated["tips"],
                "url": url_for("recipe_detail", recipe_id=recipe.id),
            }
        )

    try:
        db_session.commit()
    except SQLAlchemyError:
        db_session.rollback()
        raise
    return saved_recipes


@app.route("/recipe/<int:recipe_id>", methods=["GET", "POST"])
def recipe_detail(recipe_id):
    db_session = get_db()
    recipe = db_session.get(Recipe, recipe_id)
    if recipe is None:
        abort(404)

    user = current_user()
    if request.method == "POST":
        if user is None:
            flash("Најавете се за да оставите рецензија.", "error")
            return redirect(url_for("login", next=request.path))
        user_id = session["user_id"]
        try:
            rating = int(request.form.get("rating", ""))
        except ValueError:
            rating = 0
        body = request.form.get("body", "").strip()
        if not 1 <= rating <= 5:
            flash("Оценката мора да биде од 1 до 5.", "error")
            return redirect(url_for("recipe_detail", recipe_id=recipe.id))
        if not body or len(body) > 2000:
            flash("Рецензијата мора да содржи најмногу 2000 знаци.", "error")
            return redirect(url_for("recipe_detail", recipe_id=recipe.id))

        review = (
            db_session.query(RecipeReview)
            .filter_by(user_id=user_id, recipe_id=recipe.id)
            .first()
        )
        if review is None:
            review = RecipeReview(user_id=user_id, recipe_id=recipe.id, rating=rating, body=body)
            db_session.add(review)
        review.rating = rating
        review.body = body
        db_session.commit()
        flash("Вашата рецензија е зачувана.", "success")
        return redirect(url_for("recipe_detail", recipe_id=recipe.id))

    is_saved = False
    if user is not None:
        is_saved = get_db().query(SavedRecipe.id).filter_by(
            user_id=user.id, recipe_id=recipe.id
        ).first() is not None
    ai_result = (
        db_session.query(AIResult)
        .filter_by(recipe_id=recipe.id)
        .order_by(AIResult.created_at.desc())
        .first()
    )
    reviews = (
        db_session.query(RecipeReview)
        .filter_by(recipe_id=recipe.id)
        .order_by(RecipeReview.created_at.desc())
        .all()
    )
    own_review = None
    if user is not None:
        user_id = session["user_id"]
        own_review = next(
            (review for review in reviews if review.user_id == user_id), None
        )
    average_rating = (
        round(sum(review.rating for review in reviews) / len(reviews), 1) if reviews else None
    )
    return render_template(
        "detail.html",
        recipe=recipe,
        ai_result=ai_result,
        reviews=reviews,
        own_review=own_review,
        average_rating=average_rating,
        is_saved=is_saved,
    )


@app.route("/profile")
@signed_in_required
def profile():
    return render_template("profile.html", user=current_user())


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("auth.html", mode="register")

    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    if not re.fullmatch(r"[\w.-]{3,30}", username):
        flash("Корисничкото име мора да има 3–30 букви, бројки, точки, цртички или долни црти.", "error")
        return render_template("auth.html", mode="register"), 400
    if len(password) < 8:
        flash("Лозинката мора да содржи најмалку 8 знаци.", "error")
        return render_template("auth.html", mode="register"), 400

    db_session = get_db()
    if db_session.query(User).filter_by(username=username).first():
        flash("Ова корисничко име веќе постои.", "error")
        return render_template("auth.html", mode="register"), 400
    user = User(username=username, password_hash=generate_password_hash(password))
    db_session.add(user)
    db_session.commit()
    session.clear()
    session["user_id"] = user.id
    flash("Профилот е креиран.", "success")
    return redirect(url_for("profile"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth.html", mode="login")

    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    user = get_db().query(User).filter_by(username=username).first()
    if user is None or not check_password_hash(user.password_hash, password):
        flash("Корисничкото име или лозинката не се точни.", "error")
        return render_template("auth.html", mode="login"), 401

    session.clear()
    session["user_id"] = user.id
    flash("Успешно се најавивте.", "success")
    destination = request.args.get("next", "")
    parsed_destination = urlsplit(destination)
    if (
            not parsed_destination.scheme
            and not parsed_destination.netloc
            and parsed_destination.path.startswith("/")
            and not parsed_destination.path.startswith("//")
            and "\\" not in parsed_destination.path
    ):
        return redirect(destination)
    return redirect(url_for("profile"))


@app.route("/logout", methods=["POST"])
@signed_in_required
def logout():
    session.clear()
    flash("Се одјавивте.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
