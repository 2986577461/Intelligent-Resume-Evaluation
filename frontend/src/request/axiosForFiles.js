import { axiosResumeInstance } from "./axiosInit.js";

export const uploadFile = (file, threadId = "") => {
  const fd = new FormData();
  fd.append("file", file);
  if (threadId) fd.append("thread_id", threadId);
  return axiosResumeInstance.post("/api/files/upload", fd, {
    headers: { "Content-Type": undefined },
  });
};
