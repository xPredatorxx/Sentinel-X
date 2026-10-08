from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

# le "moteur" : la connexion à la base. L'URL contient le mot de passe :
# elle n'est jamais affichée (hide_parameters masque aussi les valeurs dans les erreurs SQL).
engine = create_engine(settings.sqlalchemy_url, pool_pre_ping=True, hide_parameters=True)

# une "fabrique" qui produit des sessions (accès temporaires à la base)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# toutes nos tables hériteront de cette classe (fichier models.py)
class Base(DeclarativeBase):
    pass


# fournit une session le temps d'une requête, puis la referme proprement
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
