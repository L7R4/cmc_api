from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    MYSQL_USER: str
    MYSQL_PASS: str
    MYSQL_HOST: str
    MYSQL_DB: str
    CORS_ORIGINS: str

    @property
    def MYSQL_URL(self) -> str:
        return (
            f"mysql+aiomysql://{self.MYSQL_USER}:"
            f"{self.MYSQL_PASS}@{self.MYSQL_HOST}/{self.MYSQL_DB}"
        )
# 
    def CORS_LIST(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(',') if o.strip()]

# Carga valores desde .env
settings = Settings(_env_file=".env")
