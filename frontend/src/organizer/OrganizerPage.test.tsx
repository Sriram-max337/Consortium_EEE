import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import OrganizerPage from './OrganizerPage'

const { apiGet, apiRequest, logout } = vi.hoisted(() => ({ apiGet: vi.fn(), apiRequest: vi.fn(), logout: vi.fn() }))
vi.mock('../auth/useAuth', () => ({ useAuth: () => ({
  principal: { name: 'Shared Organizer', email: 'organizer@example.test', principal_type: 'organizer' },
  session: { access_token: 'organizer-token' }, logout,
}) }))
vi.mock('../lib/api', () => ({ ApiError: class ApiError extends Error { status = 0; kind = 'network' }, apiGet, apiRequest }))
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }))

const ingredients = Array.from({ length: 10 }, (_, index) => ({
  id: `ingredient-${index + 1}`, name: `Ingredient ${index + 1}`, emoji: null,
}))

function setup() {
  apiGet.mockResolvedValue(ingredients)
}

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('ingredient order checker', () => {
  it('submits all ten ingredient UUIDs and shows only CORRECT', async () => {
    setup()
    apiRequest.mockResolvedValue({ result: 'CORRECT' })
    render(<OrganizerPage />)
    await screen.findByText('Ingredient 10')
    fireEvent.click(screen.getByRole('button', { name: 'Check order' }))
    expect(await screen.findByRole('status')).toHaveTextContent('CORRECT')
    expect(apiRequest).toHaveBeenCalledWith('POST', '/organizer/check-order', 'organizer-token', {
      ingredient_ids: ingredients.map((item) => item.id),
    })
  })

  it('shows only INCORRECT for a valid order in the wrong positions', async () => {
    setup()
    apiRequest.mockResolvedValue({ result: 'INCORRECT' })
    render(<OrganizerPage />)
    await screen.findByText('Ingredient 10')
    fireEvent.click(screen.getByRole('button', { name: 'Check order' }))
    expect(await screen.findByRole('status')).toHaveTextContent('INCORRECT')
    expect(screen.queryByText(/match|winner|attempt|witch/i)).not.toBeInTheDocument()
  })

  it('reorders with accessible controls and resets for the next check', async () => {
    setup()
    apiRequest.mockResolvedValue({ result: 'INCORRECT' })
    render(<OrganizerPage />)
    await screen.findByText('Ingredient 10')
    fireEvent.click(screen.getByRole('button', { name: 'Move Ingredient 2 up' }))
    expect(screen.getAllByRole('listitem')[0]).toHaveTextContent('Ingredient 2')
    fireEvent.click(screen.getByRole('button', { name: 'Check order' }))
    await screen.findByRole('status')
    fireEvent.click(screen.getByRole('button', { name: /Reset \/ New check/ }))
    expect(screen.getAllByRole('listitem')[0]).toHaveTextContent('Ingredient 1')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('shows API validation errors without displaying a result', async () => {
    setup()
    apiRequest.mockRejectedValue(new Error('The order must contain all active ingredients'))
    render(<OrganizerPage />)
    await screen.findByText('Ingredient 10')
    fireEvent.click(screen.getByRole('button', { name: 'Check order' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The order must contain all active ingredients')
    expect(screen.queryByText('CORRECT')).not.toBeInTheDocument()
    expect(screen.queryByText('INCORRECT')).not.toBeInTheDocument()
  })

  it('prevents duplicate checks while the request is pending', async () => {
    setup()
    let resolveCheck!: (value: { result: 'CORRECT' }) => void
    apiRequest.mockReturnValue(new Promise((resolve) => { resolveCheck = resolve }))
    render(<OrganizerPage />)
    await screen.findByText('Ingredient 10')
    fireEvent.click(screen.getByRole('button', { name: 'Check order' }))
    expect(screen.getByRole('button', { name: /Checking order/ })).toBeDisabled()
    expect(apiRequest).toHaveBeenCalledTimes(1)
    resolveCheck({ result: 'CORRECT' })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Check order' })).toBeEnabled())
  })
})
