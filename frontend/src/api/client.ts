import axios from "axios";

const client = axios.create({
  baseURL: "/api",
});

// Surface the backend's error detail instead of axios's generic
// "Request failed with status code 409". FastAPI returns {detail: "..."}.
client.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const detail = error?.response?.data?.detail;
    if (detail) {
      error.message = typeof detail === "string" ? detail : JSON.stringify(detail);
    }
    return Promise.reject(error);
  }
);

export default client;
