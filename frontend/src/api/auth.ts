import request from "./request";

export interface LoginParams {
  username: string;
  password: string;
}

export function loginApi(params: LoginParams) {
  return request.post("/auth/login", params);
}

export function getMeApi() {
  return request.get("/auth/me");
}
