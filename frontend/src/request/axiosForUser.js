import { axiosResumeInstance } from "./axiosInit.js";

export const register = (credentials) =>
  axiosResumeInstance.post("/api/auth/register", credentials);

export const login = (credentials) =>
  axiosResumeInstance.post("/api/auth/login", credentials);

export const getThis = () =>
  axiosResumeInstance.get("/api/auth/me", { silent: true });

export const logout = () => axiosResumeInstance.post("/api/auth/logout");
