import { useState } from 'react'
import { useMovieDetect } from '@/hooks/useMovieDetect'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Textarea'
import { MovieResultCard } from './MovieResultCard'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Film, RotateCcw } from 'lucide-react'
import { cn } from '@/lib/utils'

const EXAMPLE_CHIPS = [
  '70mm | Laser',
  '35mm Film',
  '3D | Atmos',
  '2D',
  'IMAX Laser',
  'DolbyAtmos | 4K',
  'Premium Large Format',
]

function MovieSingleDetector() {
  const [amenityString, setAmenityString] = useState('')
  const { result, loading, error, detect, reset } = useMovieDetect()

  const handleDetect = async () => {
    if (!amenityString.trim()) return
    await detect({ amenity: amenityString })
  }

  const handleChip = (chip: string) => {
    setAmenityString(chip)
    reset()
  }

  const handleReset = () => {
    setAmenityString('')
    reset()
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-violet-600/10 dark:bg-violet-600/20 flex items-center justify-center">
              <Film className="h-4 w-4 text-violet-600 dark:text-violet-400" />
            </div>
            <div>
              <CardTitle>Single Detection</CardTitle>
              <CardDescription>Detect the movie format from an amenity string</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {/* Example chips */}
          <div>
            <p className="text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-2">Try an example:</p>
            <div className="flex flex-wrap gap-1.5">
              {EXAMPLE_CHIPS.map((chip) => (
                <button
                  key={chip}
                  onClick={() => handleChip(chip)}
                  className={cn(
                    'rounded-full border px-3 py-1 text-xs font-medium transition-all duration-150',
                    amenityString === chip
                      ? 'border-violet-500 bg-violet-50 dark:bg-violet-950/30 text-violet-700 dark:text-violet-300'
                      : 'border-zinc-200 dark:border-zinc-700 text-zinc-600 dark:text-zinc-400 hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50 dark:hover:bg-zinc-800'
                  )}
                >
                  {chip}
                </button>
              ))}
            </div>
          </div>

          {/* Input */}
          <Textarea
            label="Amenity String"
            id="amenity-string"
            value={amenityString}
            onChange={(e) => {
              setAmenityString(e.target.value)
              if (result) reset()
            }}
            placeholder="e.g. 70mm Film | Laser | Dolby Atmos"
            rows={3}
          />

          {/* Actions */}
          <div className="flex items-center gap-2 pt-1">
            <Button
              onClick={() => void handleDetect()}
              loading={loading}
              disabled={!amenityString.trim()}
              className="flex-1"
            >
              <Film className="h-4 w-4" />
              Detect Format
            </Button>
            {(amenityString || result) && (
              <Button variant="ghost" size="icon" onClick={handleReset} aria-label="Reset">
                <RotateCcw className="h-4 w-4" />
              </Button>
            )}
          </div>

          {/* Error */}
          {error && (
            <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 px-4 py-3 text-sm text-red-700 dark:text-red-400">
              {error}
            </div>
          )}
        </CardContent>
      </Card>

      {result && <MovieResultCard result={result} />}
    </div>
  )
}

export { MovieSingleDetector }
