import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Layout from "./components/Layout";
import MediaListPage from "./pages/MediaListPage";
import MediaDetailPage from "./pages/MediaDetailPage";
import SubtitleJobsPage from "./pages/SubtitleJobsPage";
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
            <Route index element={<MediaListPage />} />
            <Route path="/media/:id" element={<MediaDetailPage />} />
            <Route path="/tasks" element={<SubtitleJobsPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
