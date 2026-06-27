import { axiosResumeInstance } from "./axiosInit.js";

export const getConversations = () => axiosResumeInstance.get("/api/conversations");

export const getMessages = (threadId) =>
  axiosResumeInstance.get(`/api/conversations/${threadId}/messages`);

export const deleteConversation = (threadId) =>
  axiosResumeInstance.delete(`/api/conversations/${threadId}`);
