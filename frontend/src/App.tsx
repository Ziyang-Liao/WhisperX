import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Layout from "./components/Layout";
import AudioListPage from "./pages/AudioListPage";
import AudioDetailPage from "./pages/AudioDetailPage";
import TranscriptionTasksPage from "./pages/TranscriptionTasksPage";
import "./App.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<AudioListPage />} />
            <Route path="/audio/:id" element={<AudioDetailPage />} />
            <Route path="/tasks" element={<TranscriptionTasksPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
