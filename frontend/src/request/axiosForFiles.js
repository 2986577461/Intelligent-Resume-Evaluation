import axiosInstance from "./axiosInit.js";

export const uploadFile = (file) => {
  const fd = new FormData();
  fd.append("file", file);
  return axiosInstance.post("/api/files/upload", fd, {
    headers: { "Content-Type": undefined },
  });
};
