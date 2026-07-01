import { Routes, Route, Navigate } from 'react-router-dom'
import { DetectionPage } from '@/pages/detection/DetectionPage'
import { AmenitiesPage } from '@/pages/amenities/AmenitiesPage'
import { CircuitsPage } from '@/pages/circuits/CircuitsPage'
import { ReviewQueuePage } from '@/pages/review/ReviewQueuePage'
import { SettingsPage } from '@/pages/settings/SettingsPage'

function AppRoutes() {
  return (
    <Routes>
      <Route path="/detection" element={<DetectionPage />} />
      <Route path="/amenities" element={<AmenitiesPage />} />
      <Route path="/circuits" element={<CircuitsPage />} />
      <Route path="/review" element={<ReviewQueuePage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="*" element={<Navigate to="/detection" replace />} />
    </Routes>
  )
}

export { AppRoutes }
