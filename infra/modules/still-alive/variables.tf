variable "name_prefix" {
  description = "e.g. \"kaval-prod\" or \"kaval-staging\". Must be the node's own name_prefix: it is the Environment dimension the node's heartbeat carries, and names the alarm and topic."
  type        = string
}

variable "asg_name" {
  description = "The app-server auto-scaling group (its name is the node's name_prefix). Its desired size says whether the server is meant to be running; the node module turns that metric on."
  type        = string
}

variable "alert_email" {
  description = "Where the alarm e-mails. Empty means no subscription (staging). The subscription must be confirmed by clicking the link AWS sends before any alert arrives."
  type        = string
  default     = ""
}

variable "period_seconds" {
  description = "Length of one reading. The node sends a datapoint every 2 minutes, so each 5-minute reading should hold two or three."
  type        = number
  default     = 300
}

variable "silent_periods" {
  description = "Readings in a row that must be silent-while-wanted before the alarm rings: 5 x 5 minutes = 25 minutes, long enough for the 2 min 10 s self-heal and the 5 to 8 minute bring-up."
  type        = number
  default     = 5
}

variable "tags" {
  type    = map(string)
  default = {}
}
