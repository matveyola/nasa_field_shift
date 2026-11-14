import { useLocale } from './locale'
import { ArrowPathIcon, CheckCircleIcon, ClockIcon } from '@heroicons/react/24/outline'
import type { Job } from '../api/contracts'
import { ErrorBox } from './ui'

const messages = {
  queued: 'Queued. The worker will pick up this run.',
  running: 'The worker is processing this run.',
  retrying: 'The run will be retried automatically.',
  succeeded: 'Run completed.',
}

export function JobStatus({ job, error }: { job?: Job; error?: string }) {
  const { t } = useLocale()
  if (job?.status === 'failed') return <ErrorBox message={job.error || t('This run failed.')} />
  if (error) return <ErrorBox message={t('Unable to check run status.') + ' ' + t(error)} />
  const status = job?.status
  const Icon = status === 'succeeded' ? CheckCircleIcon : status === 'running' ? ArrowPathIcon : ClockIcon
  return (
    <div className="job-status" role="status">
      <Icon className={status === 'running' ? 'spin' : ''} aria-hidden="true" />
      {status ? t(messages[status]) : t('Checking run status…')}
      {job?.attempts ? <span>{t('Attempt')} {job.attempts}</span> : null}
    </div>
  )
}
