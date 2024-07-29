run_gunicorn:
	pipenv run gunicorn --bind 0.0.0.0:8505 -w 1 --threads 1 -c=gunicorn_wsgi.py app:app
run:
	FLASK_APP=app.py pipenv run flask run -p 8505