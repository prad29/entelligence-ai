import { Routes, Route, Navigate } from 'react-router-dom'
import { DetectionPage } from '@/pages/detection/DetectionPage'
import { AmenitiesPage } from '@/pages/amenities/AmenitiesPage'
import { ReviewQueuePage } from '@/pages/review/ReviewQueuePage'
import { SettingsPage } from '@/pages/settings/SettingsPage'
import { MovieDetectionPage } from '@/pages/movie-detection/MovieDetectionPage'
import { MovieFormatsPage } from '@/pages/movie-formats/MovieFormatsPage'
import { MovieReviewQueuePage } from '@/pages/movie-review/MovieReviewQueuePage'

function AppRoutes() {
  return (
    <Routes>
      <Route path="/detection" element={<DetectionPage />} />
      <Route path="/amenities" element={<AmenitiesPage />} />
      <Route path="/review" element={<ReviewQueuePage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="/movie-detection" element={<MovieDetectionPage />} />
      <Route path="/movie-formats" element={<MovieFormatsPage />} />
      <Route path="/movie-review" element={<MovieReviewQueuePage />} />
      <Route path="*" element={<Navigate to="/detection" replace />} />
    </Routes>
  )
}

export { AppRoutes }
