import { useState } from 'react'
import { useMovieTitleMatch } from '@/hooks/useMovieTitleMatch'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Textarea'
import { Input } from '@/components/ui/Input'
import { MovieTitleMatchCard } from './MovieTitleMatchCard'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Clapperboard, RotateCcw, Search } from 'lucide-react'
import { cn } from '@/lib/utils'

const EXAMPLE_CHIPS = [
  'Moana (Live Action)',
  'MegaReelDeal Deathly Hallows: Part 1',
  'Love Island Season Finale',
  'KIDSHOW SMURFS',
  'Harry Potter 3',
  'Fight Club',
  'TOY STORY',
  '$5 Batman',
  'Night Nurse (Open Captioning)',
  'HP (OV 1-4) Germany',
]

function MovieTitleSingleMatcher() {
  const [title, setTitle] = useState('')
  const [theater, setTheater] = useState('')
  const [showDate, setShowDate] = useState('')
  const [ticketingUrl, setTicketingUrl] = useState('')
  const { result, loading, error, match, reset } = useMovieTitleMatch()

  const isNotSeeded = result !== null && result.suggested_movie_id === 0

  const handleMatch = async () => {
    if (!title.trim()) return
    await match({
      title: title.trim(),
      ...(theater.trim() ? { theater: theater.trim() } : {}),
      ...(showDate ? { show_date: showDate } : {}),
      ...(ticketingUrl.trim() ? { ticketing_url: ticketingUrl.trim() } : {}),
    })
  }

  const handleChip = (chip: string) => {
    setTitle(chip)
    reset()
  }

  const handleReset = () => {
    setTitle('')
    setTheater('')
    setShowDate('')
    setTicketingUrl('')
    reset()
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-violet-600/10 dark:bg-violet-600/20 flex items-center justify-center">
              <Clapperboard className="h-4 w-4 text-violet-600 dark:text-violet-400" />
            </div>
            <div>
              <CardTitle>Single Match</CardTitle>
              <CardDescription>Match a movie title against the Movie Master database</CardDescription>
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
                  type="button"
                  onClick={() => handleChip(chip)}
                  className={cn(
                    'rounded-full border px-3 py-1 text-xs font-medium transition-all duration-150',
                    title === chip
                      ? 'border-violet-500 bg-violet-50 dark:bg-violet-950/30 text-violet-700 dark:text-violet-300'
                      : 'border-zinc-200 dark:border-zinc-700 text-zinc-600 dark:text-zinc-400 hover:border-zinc-300 dark:hover:border-zinc-600 hover:bg-zinc-50 dark:hover:bg-zinc-800'
                  )}
                >
                  {chip}
                </button>
              ))}
            </div>
          </div>

          {/* Title input */}
          <Textarea
            label="Movie Title"
            id="movie-title"
            value={title}
            onChange={(e) => {
              setTitle(e.target.value)
              if (result) reset()
            }}
            placeholder="e.g. Moana (Live Action)"
            rows={3}
          />

          {/* Optional fields */}
          <Input
            label="Theater (optional)"
            id="theater"
            value={theater}
            onChange={(e) => setTheater(e.target.value)}
            placeholder="e.g. Falmouth Luxury Cinemas"
          />
          <Input
            label="Show Date (optional)"
            id="show-date"
            type="date"
            value={showDate}
            onChange={(e) => setShowDate(e.target.value)}
          />
          <Input
            label="Ticketing URL (optional)"
            id="ticketing-url"
            value={ticketingUrl}
            onChange={(e) => setTicketingUrl(e.target.value)}
            placeholder="https://..."
          />

          {/* Actions */}
          <div className="flex items-center gap-2 pt-1">
            <Button
              onClick={() => void handleMatch()}
              loading={loading}
              disabled={!title.trim()}
              className="flex-1"
            >
              <Search className="h-4 w-4" />
              Find Match
            </Button>
            {(title || theater || showDate || ticketingUrl || result) && (
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

      {/* Not seeded banner */}
      {isNotSeeded && (
        <div className="rounded-lg border border-amber-200 dark:border-amber-800/60 bg-amber-50 dark:bg-amber-950/30 px-4 py-3 text-sm text-amber-700 dark:text-amber-400">
          <p className="font-medium mb-1">Movie Master database not yet seeded.</p>
          <p className="text-xs font-mono">
            Run: python app/cli.py seed-movie-master /path/to/dump.csv
          </p>
        </div>
      )}

      {/* Result card (only shown when not a not-seeded case) */}
      {result && !isNotSeeded && <MovieTitleMatchCard result={result} />}
    </div>
  )
}

export { MovieTitleSingleMatcher }
