from sqlalchemy import Boolean, Column, ForeignKey, Integer, String

from app.db.base import Base


class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)
    description = Column(String(255))


class Permission(Base):
    __tablename__ = "permissions"
    id = Column(Integer, primary_key=True)
    code = Column(String(128), unique=True, nullable=False)
    description = Column(String(255))


class UserRole(Base):
    __tablename__ = "user_role"
    user_id = Column(Integer, ForeignKey("listado_medico.ID", ondelete="CASCADE"), primary_key=True)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class RolePermission(Base):
    __tablename__ = "role_permission"
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id = Column(Integer, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class UserPermission(Base):
    __tablename__ = "user_permission"
    user_id = Column(Integer, ForeignKey("listado_medico.ID", ondelete="CASCADE"), primary_key=True)
    permission_id = Column(Integer, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)
    allow = Column(Boolean, nullable=False, default=True)
