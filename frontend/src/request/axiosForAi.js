import axiosInstance from "./axiosInit.js";

export const getConversations = () => axiosInstance.get("/api/conversations");

export const getMessages = (threadId) =>
  axiosInstance.get(`/api/conversations/${threadId}/messages`);

export const deleteConversation = (threadId) =>
  axiosInstance.delete(`/api/conversations/${threadId}`);
