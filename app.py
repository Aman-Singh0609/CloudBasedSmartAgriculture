from flask import Flask, request, render_template, redirect
import mysql.connector
import joblib
import numpy as np

app = Flask(__name__, template_folder='templates', static_folder='static')

# Load ML model
model = joblib.load("crop_recommendation_model.pkl")

# ✅ Function to create a fresh database connection
def get_db_connection():
    try:
        return mysql.connector.connect(
            host='agriculture-db.cmrwmgi6krkn.us-east-1.rds.amazonaws.com',
            user='Aman',
            password='singhfamily12345',
            database='agriculture'
        )
    except mysql.connector.Error as err:
        print(f"Error: {err}")
        return None  # Return None if connection fails

@app.route('/')
def home():
    return render_template('index.html')

# ✅ SIGN UP
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form['Name']
        email = request.form['Email']
        password = request.form['Password']
        govt_id = request.form['GovtId']

        conn = None
        try:
            conn = get_db_connection()
            if conn is None:
                return "Signup failed: Database connection not available."

            cur = conn.cursor()
            cur.execute("INSERT INTO users (Name, Email, Password, GovtId) VALUES (%s, %s, %s, %s)",
                        (name, email, password, govt_id))
            conn.commit()
            return redirect("/croprec")
        except mysql.connector.IntegrityError:
            return "Email already registered. Try logging in."
        except Exception as e:
            return f"Signup failed: {e}"
        finally:
            if 'cur' in locals() and cur is not None:
                cur.close()
            if conn is not None:
                conn.close() # Always close the connection

    return render_template("signup.html")

# ✅ LOGIN
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form['Email']
        password = request.form['Password']

        conn = None
        try:
            conn = get_db_connection()
            if conn is None:
                return "Login failed: Database connection not available."

            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE Email=%s AND Password=%s", (email, password))
            user = cur.fetchone()

            if user:
                return redirect("/croprec")
            else:
                return "Invalid login credentials"
        except Exception as e:
            return f"Login failed: {e}"
        finally:
            if 'cur' in locals() and cur is not None:
                cur.close()
            if conn is not None:
                conn.close() # Always close the connection

    return render_template("login.html")

# ✅ Crop Recommendation Page
@app.route("/croprec", methods=["GET"])
def croprec():
    return render_template("cropRec.html")

# ✅ Predict Endpoint
@app.route('/predict', methods=['POST'])
def predict():
    try:
        N = float(request.form['N'])
        P = float(request.form['P'])
        K = float(request.form['K'])
        temperature = float(request.form['temperature'])
        humidity = float(request.form['humidity'])
        ph = float(request.form['ph'])
        rainfall = float(request.form['rainfall'])

        features = np.array([[N, P, K, temperature, humidity, ph, rainfall]])
        prediction = model.predict(features)
        predicted_crop = prediction[0]

        return render_template('result.html', recommendation={
            'name': predicted_crop.title(),
            'summary': f"Based on your inputs, we recommend planting {predicted_crop.title()}."
        })

    except Exception as e:
        return f"Prediction failed: {e}"

if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=True)