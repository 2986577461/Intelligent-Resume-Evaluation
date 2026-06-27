import axiosInstance from "./axiosInit.js";

export const login = async (loginMessage) =>
  axiosInstance.post("/user/users/login", loginMessage);

export const getThis = async () => axiosInstance.get("/user/users");
