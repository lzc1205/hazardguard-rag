import { defineStore } from "pinia";
import { ref } from "vue";
import { loginApi, getMeApi } from "@/api/auth";

export interface UserInfo {
  id: string;
  username: string;
  email: string | null;
  full_name: string | null;
  role: string;
  organization: string | null;
  is_active: boolean;
}

export const useUserStore = defineStore("user", () => {
  const token = ref<string>(localStorage.getItem("token") || "");
  const userInfo = ref<UserInfo | null>(null);

  const isLoggedIn = () => !!token.value;

  async function login(username: string, password: string) {
    const res = await loginApi({ username, password });
    token.value = res.data.access_token;
    localStorage.setItem("token", res.data.access_token);
    userInfo.value = res.data.user;
    return res;
  }

  async function fetchUserInfo() {
    const res = await getMeApi();
    userInfo.value = res.data;
  }

  function logout() {
    token.value = "";
    userInfo.value = null;
    localStorage.removeItem("token");
  }

  return { token, userInfo, isLoggedIn, login, fetchUserInfo, logout };
});
