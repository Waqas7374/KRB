import { create } from "zustand";

export interface ToastItem {
  id: number;
  kind: "success" | "error";
  message: string;
}

interface ToastState {
  toasts: ToastItem[];
  push: (kind: ToastItem["kind"], message: string) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  push: (kind, message) => {
    const id = nextId++;
    set({ toasts: [...get().toasts.slice(-3), { id, kind, message }] });
    // Errors stay until dismissed: a failure that disappears before it is
    // read is a failure the user never knew about.
    if (kind === "success") setTimeout(() => get().dismiss(id), 4000);
  },
  dismiss: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
}));

export const toast = {
  success: (message: string) => useToastStore.getState().push("success", message),
  error: (message: string) => useToastStore.getState().push("error", message),
};
